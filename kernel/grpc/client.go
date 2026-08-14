// Package grpc 调度器侧的远端 routine server 代理(dial-out *Client)+ kernel 对外
// grpc server(dial-in *ServerConn).gRPC 是 conn 抽象的一种传输实现.
//
// 连接 python routine server(gRPC):
//   - Req unary:只做查询(get_modules / get_routines / ...)
//   - Stream 双向流:lifecycle 控制(start/stop)与回报(started/stopped)
//
// 一条 Stream 长连接复用所有 routine.入站全走 bus:reader 收 Stream 回报 → publish
// 到 conn.event(零 future/零业务知识,future 解析在 shell.Manager + node).出站全走
// bus:上层 publish 到 conn.out.<connID>,本 conn 的 sendLoop 订阅,调 stream.Send,
// 失败 publish conn.outfail(Manager resolve future chan with err).
//
// 传输无关契约不在本包----在 kernel/conn(Conn 接口 + CreatedResult + 协议词汇 +
// dict helper + bus topic 名与载荷).进程内事件总线在 kernel/bus(通用 pub/sub).
// 本包只实现 gRPC 传输:把 conn.out 上的 OutMsg 编成 gRPC Stream Send,把 Stream
// Recv 的回报 publish 到 conn.event.
//
// 本包按职责拆成多个同包文件:
//   - client.go(本文件):*Client(dial-out)类型 + New/Start/Close + sendLoop
//   - server.go:*ServerConn(dial-in)+ kernel grpc server + sendLoop
//   - connect.go:dial-out 连接生命周期(monitorConnect/connect/onStreamError/send)
//   - reader.go:Stream 入站回报 → bus.Publish(纯 publish,不解析 future)
//   - helpers.go:Req 查询
//
// 编排(broker pubsub/message/body + 反向 submit/start/stop dispatch + future
// chan 解析)中央化在 shell.Manager----它订阅 bus,不经过 grpc 包.grpc 包零 shell
// 知识:入站全走 bus,出站全走 bus.
package grpc

import (
	"fmt"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/backoff"
	"google.golang.org/grpc/credentials/insecure"

	"kernel/bus"
	"kernel/conn"
)

// clientIDCounter 进程内 client id 自增计数器(对标老版 utils.GetIdGenerator("client")).
// 第一个 client = "1",第二个 = "2",....不跨进程稳定----但 client 实例在断线重连
// 全程不销毁(只重建 stream),进程内 id 稳定,重连 reload 能正确路由.
var clientIDCounter uint64

// 编译期断言:*Client 实现 conn.Conn 接口.
var _ conn.Conn = (*Client)(nil)

// Client 远端 routine server 的 gRPC 代理.
type Client struct {
	id      string // client id(进程内自增),用于 routine 挂载 + 精准卸载定位 + 出站 topic
	conn    *grpc.ClientConn
	service RoutineServiceClient
	addr    string

	stream   RoutineService_StreamClient
	streamMu sync.Mutex // 保护 Stream.Send(可能阻塞,独立于 stateMu,不能合)

	// stateMu 保护 streamReady chan + closed 标志.streamReady 是 send 的就绪信号.
	// future chan 不在此(在 shell.node)----conn 零 future 知识.
	stateMu sync.Mutex

	streamReady chan struct{} // 开 Stream 后 close,断线重建.send 在 stream==nil 时等它

	outSub *bus.Subscriber // 出站订阅(Start 时建,Close 时关).先订阅再起 sendLoop,
	// 避免上层 publish 出站撞上 sub 未建→丢

	closed bool
}

// New 连接远端 routine server(明文),起一条 Stream 并
// 起 reader 分发回报 + sendLoop 发出站.opts 追加到默认 dial 选项(如 bufconn 的
// WithContextDialer).
//
// 不因 server 暂时没起就报错:grpc.NewClient 是惰性连接,底层带 backoff 自动重试;
// Stream 在连接 Ready 后才开(见 monitorConnect).server 后起来会自动连上.
// 只有 NewClient 本身配置错误才返回 error.
func New(addr string, opts ...grpc.DialOption) (*Client, error) {
	defaultOpts := []grpc.DialOption{
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		// 注意:不加 WithKeepaliveParams----client 不主动发
		// keepalive ping,用 grpc 默认行为.加了(PermitWithoutStream=true + Time=10s)
		// 会让 client 无活动 stream 时也每 10s 发 ping,触发 server "too_many_pings" →
		// GoAway ENHANCE_YOUR_CALM 导致 stream 断线重连.断线检测靠 TCP 超时.
		// 底层连接重连策略:server 没起时按 backoff 自动重试,
		// 不让上层每次手动重连.BaseDelay 200ms ×1.5 封顶 5s.
		grpc.WithConnectParams(grpc.ConnectParams{
			Backoff: backoff.Config{
				BaseDelay:  200 * time.Millisecond,
				Multiplier: 1.5,
				Jitter:     0.2,
				MaxDelay:   5 * time.Second,
			},
			MinConnectTimeout: 5 * time.Second,
		}),
	}
	opts = append(defaultOpts, opts...)
	cc, err := grpc.NewClient(addr, opts...)
	if err != nil {
		return nil, fmt.Errorf("dial %s: %w", addr, err)
	}
	id := strconv.FormatUint(atomic.AddUint64(&clientIDCounter, 1), 10)
	c := &Client{
		id:          id,
		conn:        cc,
		service:     NewRoutineServiceClient(cc),
		addr:        addr,
		streamReady: make(chan struct{}),
	}
	// 不在此触发连接----留给 Start().连接生命周期事件(up/down)经 bus publish,
	// Manager 订阅处理(拉 catalog + 起 passive / 卸载死节点),不再靠回调字段.
	return c, nil
}

// Start 开始连接 routine server:订阅出站 topic(先于 sendLoop,避免首条出站丢)+
// 触发底层连接 + 起 monitorConnect 监控状态 + 起 sendLoop.首次 Ready 时 connect()
// 会 bus.Publish(TopicConn{up}),Manager 订阅处理 catalog 拉取 + passive 自动拉起.
func (c *Client) Start() {
	c.outSub = bus.GetBus().Subscribe(conn.TopicOut+"."+c.id, 256, false)
	c.conn.Connect()
	go c.monitorConnect()
	go c.sendLoop(c.outSub)
}

func (c *Client) Close() error {
	c.stateMu.Lock()
	c.closed = true
	c.stateMu.Unlock()
	if c.outSub != nil {
		c.outSub.Close()
	}
	return c.conn.Close()
}

func (c *Client) Address() string { return c.addr }

// WaitReady 等 stream 真正连上就绪(首次连接握手完成 + reader 起来).短命模式
// (runDemo/runXsA)拉 catalog 前调它,避免 c.Start() 后立即 Req 撞上 lazy-connect
// 握手未完 → catalog 拉空 → routine not registered.超时(30s)或 Close 后返回 false.
//
// 仅用于短命验证脚本(该先起 server).常驻模式不调它----靠 conn.up 事件驱动
// 拉 catalog + 起 passive,谁先启动都行,后起的 server 自动连上.
func (c *Client) WaitReady() bool {
	return c.waitStreamReady(30 * time.Second)
}

// ID 返回 client id(进程内自增,断线重连不变).routine 节点挂这个 id,出站 topic
// 用它(conn.out.<id>),断线时按它精准卸载该 client 名下的 routine.
func (c *Client) ID() string { return c.id }

// DialIn dial-out conn(kernel→routine),catalog 走 Req 拉.
func (c *Client) DialIn() bool { return false }

func (c *Client) isClosed() bool {
	c.stateMu.Lock()
	defer c.stateMu.Unlock()
	return c.closed
}

// sendLoop 订阅本 conn 的出站 topic(conn.out.<id>),逐条调底层 send 发到 wire.
// 单 goroutine → per-conn 出站串行化(保序,sends 快无阻塞).send 失败 publish
// conn.outfail(带 Msg 的 id/event),让 Manager resolve 对应 routine 的 future chan
// with err----错误经 bus 回流,不丢,tracer 可见.Close 后退出.
func (c *Client) sendLoop(sub *bus.Subscriber) {
	for payload := range sub.Recv() {
		out, ok := payload.(conn.OutMsg)
		if !ok {
			continue
		}
		if err := c.send(out.Msg); err != nil {
			c.publishOutFail(out.Msg)
		}
	}
}

// publishOutFail 从 Msg 提取 id/event,publish OutFail 让 Manager resolve future chan.
func (c *Client) publishOutFail(msg map[string]any) {
	id, _ := msg["id"].(string)
	ev, _ := msg["event"].(string)
	bus.GetBus().Publish(conn.TopicOutFail, conn.OutFail{ConnID: c.id, ID: id, Event: ev})
}
