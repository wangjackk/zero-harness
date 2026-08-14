package shell

import (
	"context"
	"strconv"

	"kernel/command"
)

// runRemote 通过 conn 代理驱动远端 routine server 跑 cmd.Name 这条 routine.
// id 用 command 的 int ID 转字符串(python routine 用 string id).conn 从
// node.clientID 取----routine 挂在哪个 server/conn 就用那个 conn 驱动.
//
// 出站全走 bus:sendCreated/sendStart/sendStop publish 到 conn.out.<clientID>,
// 该 conn 的 sendLoop 发送.send 失败经 sendLoop→conn.outfail→Manager resolve
// node chan with err 回流----调用方 select 收到 err 返回,不丢,tracer 可见.
//
// future chan 在 node(createdCh/startedCh/stoppedCh,Create 时建好).Manager
// dispatchEvent 收到回报时 resolve 它们;conn.down 的 failPending / outfail 也 resolve.
// 非阻塞 resolve(cap1 + select default):多源先到先得.
//
// 统一路径(跟 submit/start 回环一致):created → started → stopped.
//  1. (仅 !createdSent 路径)发 lifecycle.created + 等 created 回报(带 modules 填 declared)
//  2. 发 lifecycle.start + 等 started(先到 stopped = start 失败)
//  3. 等 stopped(自然结束);ctx 取消(command.Stop)则发 lifecycle.stop 再等 stopped
//
// submit 路径(OnSubmitCreated 已发 created 并 resolve 过 createdCh,设 createdSent=true)
// 跳过 created 阶段----否则重复发 + 重复 resolve = 泄漏.Execute 直启走 !createdSent.
//
// body 退出(自然结束或被打断)时全量释放该 routine 占的所有模块.Release 按 rid
// 全量遍历,不依赖 cmd.Modules(那只含静态声明),所以无条件调.
func (m *Manager) runRemote(ctx context.Context, cmd *command.Command) {
	defer m.tree.Release(cmd.ID)
	// 取这条 routine 所属的 conn(root 不会走这里----root 不调 runRemote).
	m.mu.Lock()
	n := m.nodes[cmd.ID]
	clientID := ""
	if n != nil {
		clientID = n.clientID
	}
	createdSent := n != nil && n.createdSent
	kwargs := cmd.Kwargs
	m.mu.Unlock()
	if clientID == "" || m.Conn(clientID) == nil {
		m.log.Errorf("routine %s: conn not found", cmd)
		return
	}
	id := strconv.Itoa(cmd.ID)
	// stopped 提前就绪(node.stoppedCh,Create 时建):created 期间若 created 失败,
	// resolveStopped 走 createdCh(带 err),不碰 stoppedCh.
	stopped := n.stoppedCh
	if !createdSent {
		// Execute 直启路径:runRemote 负责 created 阶段(发 created + 等回报).
		// submit 路径 OnSubmitCreated 已发 created 并等过回报,跳过----否则重复发 +
		// 重复 resolve(Manager 在 submit 阶段已 resolve createdCh)= 泄漏.
		created := n.createdCh
		m.sendCreated(clientID, id, cmd.Name, kwargs, strconv.Itoa(cmd.ParentCommandID))
		select {
		case r := <-created:
			if r.Err != nil {
				return // created 失败(stopped during created / outfail / conn.down)
			}
			// created 回报带 modules(created() 返回值)----存进 n.declared,Start 用.
			m.mu.Lock()
			n.declared = r.Modules
			m.mu.Unlock()
		}
	}
	// lifecycle.start(不带 kwargs----py 侧 start() 用 created 时存的 _init_kwargs)
	m.sendStart(clientID, id, cmd.Name)
	// 等 started(或先到 stopped = start 失败)
	select {
	case <-stopped:
		return
	case <-n.startedCh:
	}
	cmd.MarkRunning() // lifecycle.started 回报过,starting → running
	// 等 stopped(自然结束或被打断)
	select {
	case <-ctx.Done():
		// force 驱逐时 node 上带 stopReason="force" + stopBy=evictorID(见 stop(forceBy)).
		// 透传给 py:on_done(reason='force', detail='evicted by ...') 做紧急退让.
		m.mu.Lock()
		reason, by := n.stopReason, n.stopBy
		m.mu.Unlock()
		m.sendStop(clientID, id, reason, strconv.Itoa(by))
		<-stopped
	case <-stopped:
	}
}
