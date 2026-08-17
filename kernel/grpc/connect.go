package grpc

import (
	"context"
	"fmt"
	"time"

	"google.golang.org/grpc/connectivity"

	"kernel/bus"
	"kernel/conn"
	"kernel/logger"
)

// monitorConnect 监控底层连接状态:Idle/TransientFailure 时主动 Connect,
// Ready 时开 Stream + 起 reader(见 connect).断线(Ready→非 Ready)时清 stream
// 让 send 报 reconnecting,等下次 Ready 重开.
//
// 状态变化打 log(对标老版 🔄/✅/❌/😴),让运行时可见:连上哪个 server,是否断开.
// 连接生命周期事件经 bus publish(TopicConn up/down),Manager 订阅处理 catalog
// 拉取 + passive 拉起 / 卸载死节点 + fail future chan----不再用 onDisconnect/onReconnect
// 回调字段.
func (c *Client) monitorConnect() {
	log := logger.GetLogger().Named("rpc")
	ctx := context.Background()
	last := connectivity.Idle
	for {
		state := c.conn.GetState()
		if state != last {
			switch state {
			case connectivity.Idle:
				log.Infof("😴 client %s (%s) 空闲", c.id, c.addr)
			case connectivity.Connecting:
				log.Infof("🔄 client %s (%s) 正在连接...", c.id, c.addr)
			case connectivity.Ready:
				log.Infof("✅ client %s (%s) 已连接", c.id, c.addr)
				// 连接就绪且 stream 还没开 → 开 Stream + 起 reader.
				// stream 已存在(前一轮 Ready 开的,中间没断)则不重复开.
				c.streamMu.Lock()
				needConnect := c.stream == nil
				c.streamMu.Unlock()
				if needConnect {
					c.connect()
				}
			case connectivity.TransientFailure:
				log.Warnf("❌ client %s (%s) 连接断开,正在重连...", c.id, c.addr)
				c.streamMu.Lock()
				c.stream = nil
				c.streamMu.Unlock()
			case connectivity.Shutdown:
				log.Infof("🛑 client %s (%s) 已关闭", c.id, c.addr)
			}
			last = state
		}
		if state == connectivity.Shutdown {
			return
		}
		// 主动重连:Idle 时 grpc 不自动连,需 Connect 触发.
		if state == connectivity.Idle {
			c.conn.Connect()
		}
		ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
		changed := c.conn.WaitForStateChange(ctx, state)
		cancel()
		if !changed {
			if c.conn.GetState() == connectivity.Shutdown {
				return
			}
			continue
		}
	}
}

// connect 开一条 Stream + 起 reader.连接刚 Ready 时 Stream() 可能仍失败
// (握手竞争)---- 失败则按 backoff 重试,直到开成功(起 reader)或 Close.
// 在 monitorConnect goroutine 里调,阻塞它没关系(monitor 本就是长循环).
// reader 退出(Recv err)走 onStreamError:清 stream=nil + publish conn.down +
// conn.Connect,monitor 检测到非 Ready→Ready 后重新进 connect.
func (c *Client) connect() {
	backoff := 200 * time.Millisecond
	for {
		if c.isClosed() {
			return
		}
		st, err := c.service.Stream(context.Background())
		if err == nil {
			c.streamMu.Lock()
			c.stream = st
			c.streamMu.Unlock()
			// 唤醒所有等 streamReady 的 send.
			c.stateMu.Lock()
			select {
			case <-c.streamReady:
			default:
				close(c.streamReady)
			}
			c.stateMu.Unlock()
			go c.reader()
			// 通知上层 conn 连上(含首次 + 重连):Manager 订阅拉 catalog + 起 passive.
			// 同步 publish(bus.Publish 只 put 进 Manager 的 buffered chan,不阻塞----
			// 慢 reload 在 Manager 订阅 goroutine 跑,不阻塞 monitorConnect).
			bus.GetBus().Publish(conn.TopicConn, conn.ConnChange{Kind: "up", ConnID: c.id, IsDialIn: false})
			return
		}
		// server 刚 Ready 又退 / 握手竞争:短退避后重试.底层 backoff 会
		// 重新拉起连接,下一轮 Stream() 会成功.
		time.Sleep(backoff)
		if backoff < 5*time.Second {
			backoff = time.Duration(float64(backoff) * 1.5)
		}
	}
}

// onStreamError Stream 断线处理:标记断开(send 报错)→ publish conn.down 让
// Manager failPending(resolve 该 conn 名下 routine 的 future chan with err,解阻塞
// runRemote)+ UnloadRemote(卸载死节点)→ 等 monitorConnect 检测到底层重连 Ready
// 后重新 connect.Close 后不处理.
//
// future chan 不在此(在 shell.node)----本处只 publish 生命周期事件,Manager 接管 fail.
// 重连本身由 grpc 底层 backoff + monitorConnect 管,这里不自己重开 Stream,
// 避免两条重连路径竞争重复开 Stream.
func (c *Client) onStreamError() {
	if c.isClosed() {
		// 主动 Close 触发的 Recv err,正常退出,不打印,不重连
		c.streamMu.Lock()
		c.stream = nil
		c.streamMu.Unlock()
		return
	}
	logger.GetLogger().Named("rpc").Warn("stream disconnected, waiting for reconnect")
	c.streamMu.Lock()
	c.stream = nil
	c.streamMu.Unlock()
	// 通知上层:fail future chan(解阻塞 runRemote)+ 卸载死节点 + 推缩小视图.
	// bus 投到 Manager 生命周期 chan----单 goroutine FIFO 消费,conn.down 一定在
	// 下次 conn.up(reload)前处理完,无残留死节点 / stale 视图竞态.
	bus.GetBus().Publish(conn.TopicConn, conn.ConnChange{Kind: "down", ConnID: c.id, IsDialIn: false})
	// 重建 streamReady:旧 chan 已 close,换新 chan 让 send 重新等待下一次连接.
	c.stateMu.Lock()
	c.streamReady = make(chan struct{})
	c.stateMu.Unlock()
	// 主动触发重连(Idle 时 grpc 不自动连);monitorConnect 检测到 Ready
	// 后会重新 connect(开 Stream + 起 reader).
	c.conn.Connect()
}

// send 出站:mapToFrame + stream.Send.被 sendLoop 调(消费 conn.out topic).
// stream 没就绪(首次连接 / 断线重连中):等 streamReady,不立即丢.超时(30s)兜底
// 防永久阻塞;Close 后不等直接报错(sendLoop publish OutFail,Manager resolve chan).
func (c *Client) send(msg map[string]any) error {
	f, err := mapToFrame(msg)
	if err != nil {
		return err
	}
	if !c.waitStreamReady(30 * time.Second) {
		return fmt.Errorf("client disconnected (reconnecting)")
	}
	c.streamMu.Lock()
	defer c.streamMu.Unlock()
	if c.stream == nil {
		return fmt.Errorf("client disconnected (reconnecting)")
	}
	return c.stream.Send(f)
}

// waitStreamReady 等 stream 就绪(connect 成功 close streamReady).
// 超时或 Close 后返回 false.stream 已就绪时立即返回 true.
func (c *Client) waitStreamReady(timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for {
		c.stateMu.Lock()
		ready := c.streamReady
		c.stateMu.Unlock()
		select {
		case <-ready:
			return true
		default:
		}
		if c.isClosed() {
			return false
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return false
		}
		// 短轮询:chan 可能被 onStreamError 重建,每次重新取最新引用.
		select {
		case <-ready:
			return true
		case <-time.After(min(remaining, 200*time.Millisecond)):
		}
	}
}
