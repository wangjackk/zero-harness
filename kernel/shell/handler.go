package shell

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"kernel/bus"
	"kernel/command"
	"kernel/conn"
	"kernel/module"
)

// nodeName 返回 id 对应 routine 的 name(用于日志);node 不存在时退回数字 id.
// 失败路径也要打日志,node 可能已不在----退回数字保证日志总能打出来.
func (m *Manager) nodeName(id int) string {
	m.mu.Lock()
	defer m.mu.Unlock()
	if n, ok := m.nodes[id]; ok {
		return n.cmd.Name
	}
	return strconv.Itoa(id)
}

// --- submit 跨 server 协调 ---
// submit 来源 client(python 在那发 submit 并等 submitted 回执)跟子 routine 所属的
// client(created 要发去那,created 回报从那回)可能不同.Manager 用全局 clients
// 协调:OnSubmitCreated 用子 routine 的 client 发 created(经 bus)+ 返回 node.createdCh;
// OnSubmitSubmitted 用来源 client 发 submitted 回执(经 bus).

// OnSubmitCreated 用 childID 所属的 conn(node.clientID)发 lifecycle.created,
// 返回 node.createdCh(created 回报 chan,带 modules).跨 server 正确路由:子 routine
// 在哪个 server 就 publish 到那个 conn 的出站 topic,回报从那个 conn 的 reader 经 bus 回.
// kwargs 是 submit 入参:存入 cmd.Kwargs(start 阶段也用这同一份),同时经 lifecycle.created
// 投递给 created().这样 py 侧 start() 收到的就是 submit 的 kwargs.
//
// created chan 已在 Create 时建好(node.createdCh)----这里只发 created 命令 + 返回 chan.
// send 失败不在此返回(bus fire-and-forget):经 sendLoop→outfail→Manager resolve
// createdCh with err 回流,调用方 select 收到 err 返回.
func (m *Manager) OnSubmitCreated(childID int, name string,
	kwargs map[string]any, parentID int) (<-chan conn.CreatedResult, error) {
	m.mu.Lock()
	n := m.nodes[childID]
	var clientID string
	if n != nil {
		clientID = n.clientID
		// 存入 cmd.Kwargs:runRemote 发 lifecycle.start 时带这同一份给 start().
		n.cmd.Kwargs = kwargs
	}
	m.mu.Unlock()
	if clientID == "" || m.Conn(clientID) == nil {
		return nil, fmt.Errorf("routine %s-%d: conn not found", name, childID)
	}
	cid := strconv.Itoa(childID)
	// 标记 created 已发:runRemote 跳过 created 阶段(避免重复发 + 重复 resolve chan).
	m.mu.Lock()
	if n != nil {
		n.createdSent = true
	}
	m.mu.Unlock()
	m.sendCreated(clientID, cid, name, kwargs, strconv.Itoa(parentID))
	return n.createdCh, nil
}

// OnSubmitSubmitted 发 routine.submitted 回执给 submit 来源 conn(sourceClientID).
// python 在那等 req_id 的回执拿 (child_id, modules)(submitErr != nil 时这俩不填).
// modules 是 created 回报带回的(created() 返回值,static=固定 list,dynamic=kwargs
// 现算),带给父 handle 供编排器算冲突.
func (m *Manager) OnSubmitSubmitted(sourceClientID, reqID string,
	childID int, modules []string, submitErr error) {
	reply := map[string]any{"event": conn.RoutineSubmitted, "req_id": reqID}
	if submitErr != nil {
		reply["error"] = submitErr.Error()
	} else {
		reply["child_id"] = strconv.Itoa(childID)
		if modules != nil {
			// structpb.NewStruct 要 []any 不能 []string----转一道.
			reply["modules"] = conn.ToAnySlice(modules)
		}
	}
	m.sendOut(sourceClientID, reply)
}

// OnRelayToParent 把 lifecycle started/stopped 事件中转给 childID 的父 routine
// (跨 server:父可能在另一个 server).父在哪个 server 就 publish 到那个 conn 的
// 出站 topic----唤醒父 handle 的 wait_started/wait.没父(root 直驱)则丢弃.
func (m *Manager) OnRelayToParent(childID int, msg map[string]any) {
	m.mu.Lock()
	n := m.nodes[childID]
	var clientID string
	if n != nil && n.parent != nil {
		clientID = n.parent.clientID
	}
	m.mu.Unlock()
	if clientID != "" {
		m.sendOut(clientID, msg)
	}
}

// --- 入站事件 dispatch(从 rpc reader 搬来,经 bus 触发)---

// OnSubmit 处理 python 的 routine.submit:建子命令(created 态,不 Start),
// 返回 child_id(= command id,python 用作 handle id).
//
// Create 带 kwargs(存 cmd.Kwargs,runRemote 经 lifecycle.created 投递给 created()).
// start 的 kwargs 沿用 cmd.Kwargs(OnStartChild 传 nil)----submit kwargs 是 start
// 的唯一入参来源.
//
// modules 不由 python 传,不由 catalog 缓存----实例级,由 created() 返回经 created
// 回报回带(handleReverseSubmit 的 go func 等 createdCh → OnSubmitCreatedModules 取
// 带给父 handle).子 routine 归属由 name 决定(定义在哪个 server),不继承父.
// 所以 a@serverA submit b@serverB 时 b 的 clientID 指向 serverB,跨 server submit.
//
// 拒绝 passive routine 手动 submit:is_passive=True 的 routine 生命周期归 kernel
// 管(AutoStartPassive 拉起 + passiveStarted 单实例去重),业务手动 submit 会绕过
// 去重起第二个实例.拒绝在 OnSubmit 层做(早失败,不发 created)----Create /
// Execute / AutoStartPassive 这些 internal 路径不受影响,仍可拉起 passive.
func (m *Manager) OnSubmit(parentID int, name string,
	kwargs map[string]any) (int, error) {
	// passive 拦截:查路由表 isPassive 标志,拒绝手动 submit.
	if rt := m.routeOf(name); rt.clientID != "" && rt.isPassive {
		err := fmt.Errorf("routine %s is passive (auto-started by kernel); manual submit is not allowed", name)
		m.log.Warnf("⚠️ submit %s rejected: %v", name, err)
		return 0, err
	}
	// Create 建 created 态命令(n.declared 空).modules 由 created 回报回带
	// (handleReverseSubmit 的 go func 等 createdCh → OnSubmitCreatedModules 取).
	cmd, err := m.Create(parentID, name, kwargs)
	if err != nil {
		m.log.Errorf("➡️ submit %s failed: %v", name, err)
		return 0, err
	}
	m.log.Infof("➡️ submit %s", cmd)
	return cmd.ID, nil
}

// OnStartChild 处理 python 的 routine.start:start 子命令(冲突检测+占用+运行).
// 不带 kwargs----start 用 cmd.Kwargs(submit 回环时 OnSubmitCreated 存入 / Execute
// 直启时 Create 存入),即 submit kwargs 的单一来源.硬约束:父 routine 必须
// Started(StateRunning)才能 start 子----否则返回 error,reader 会发 routine.rejected
// 回 py,py 的 handle.start()/try_start() 抛异常.
//
// try=false(start):失败时清 node+订阅(全有或全无----占不上就当没 start).
// Python 侧同样清 created instance.handle 失败后不可重试(已清理).
// try=true(try_start):失败时不清----保留 node/instance/订阅让 Python 能重试
// (占住者释放后第二次 start 能成功).真正清理走 stop/级联/断线.
func (m *Manager) OnStartChild(childID int, try bool) error {
	name := m.nodeName(childID)
	op := "▶️ start"
	if try {
		op = "▶️ try_start"
	}
	if err := m.requireParentRunning(childID, "start"); err != nil {
		if !try {
			m.cleanupFailedStart(childID)
		}
		m.log.Errorf("%s %s-%d failed: %v", op, name, childID, err)
		return err
	}
	if err := m.Start(childID, nil); err != nil {
		if !try {
			m.cleanupFailedStart(childID)
		}
		m.log.Errorf("%s %s-%d failed: %v", op, name, childID, err)
		return err
	}
	m.log.Infof("%s %s-%d", op, name, childID)
	return nil
}

// cleanupFailedStart 清理 start(非 try)失败的 routine:created 阶段已 auto_subscribe
// 发了订阅给 kernel(OnRoutineStopped 清 pubsub 表)+ 建了 node(removeNode 删).
// 模块没占(Start 的 TryAcquire 失败不占),不用 Release.
func (m *Manager) cleanupFailedStart(childID int) {
	m.OnRoutineStopped(strconv.Itoa(childID))
	m.removeNode(childID)
}

// OnUnsubmitChild 处理 python 的 routine.unsubmit:撤销提交(清 created 态子命令).
// 跟 submit 对称----submit 建 created 态,unsubmit 清 created 态.
// 硬约束:routine 必须**未 start**(created 态)----已 start 报错(该用 stop).
// 不要求父 started(submit 也不要求,created 即可).
// 成功后发 lifecycle.stopped 给 py(复用 stop 的 wire + Python 清理路径:child_ack
// resolve + on_inbound 清 instance)----unsubmit 对 py 侧等价于"子 stopped".
func (m *Manager) OnUnsubmitChild(childID int) error {
	m.mu.Lock()
	n, ok := m.nodes[childID]
	m.mu.Unlock()
	if !ok {
		return nil // 已清(idempotent)
	}
	if st := n.cmd.State(); st != command.StateCreated {
		err := fmt.Errorf("cannot unsubmit routine %s: already started (state=%s), use stop instead",
			n.cmd, st)
		m.log.Errorf("↩️ unsubmit %s failed: %v", n.cmd, err)
		return err
	}
	// created 态:清 node + 订阅(auto_subscribe 发的).模块没占(created 不占).
	m.cleanupFailedStart(childID)
	// 发 lifecycle.destroy 让 py 销毁 created instance(handle_destroy _cleanup + 回 stopped).
	// 用子 routine 所属的 conn 发(跨 server 正确路由,经 bus 出站).
	m.sendDestroy(n.clientID, strconv.Itoa(childID))
	m.log.Infof("↩️ unsubmit %s", n.cmd)
	return nil
}

// OnStopChild 处理 python 的 routine.stop:级联停子命令.
// 异步执行----本方法在 rpc reader goroutine 里被调,cmd.Stop 会阻塞等
// lifecycle.stopped,而 stopped 又要经同一 reader 投递,同步调会死锁.
// 起独立 goroutine 跑 stop,reader 立即返回继续投递 stopped.
// 硬约束:父 routine 必须 Started 才能 stop 子----否则 reader 发 routine.rejected.
func (m *Manager) OnStopChild(childID int) error {
	if err := m.requireParentRunning(childID, "stop"); err != nil {
		m.log.Errorf("🛑 stop %s-%d failed: %v", m.nodeName(childID), childID, err)
		return err
	}
	m.mu.Lock()
	n, ok := m.nodes[childID]
	if !ok || n.stopping {
		m.mu.Unlock()
		return nil // 已停 / 已在停
	}
	n.stopping = true
	m.mu.Unlock()
	// 显式 stop(handle.stop()):同步设 stopping 让可能竞态的 OnRoutineTerminated
	// 跳过 "stopped" 日志(避免 "stop" + "stopped" 重复).
	m.log.Infof("🛑 stop %s", n.cmd)
	go m.stop(n, false, 0)
	return nil
}

// OnAcquire 处理 python 的 routine.acquire:运行时占领模块.
// 底层跟 Start 静态声明同一 TryAcquire,只是触发在 start() 体里(用户主动调).
// 硬约束:本 routine 必须 Started(starting/running 都行)----ready 阶段调会返回 error.
// ancestorIDs 跟 Start 同一套算,允许父子共占(holders 队列叠加).
// 成功后并集进 cmd.Modules(让 Modules 反映 routine 当前占的全部模块).
func (m *Manager) OnAcquire(id int, modules []string) error {
	if err := m.requireSelfRunning(id, "acquire"); err != nil {
		return err
	}
	m.mu.Lock()
	n := m.nodes[id]
	ancestorIDs := ancestorIDSet(n)
	m.mu.Unlock()
	if err := m.tree.TryAcquire(id, modules, ancestorIDs); err != nil {
		return err
	}
	n.cmd.AddModules(modules...)
	return nil
}

// OnRelease 处理 python 的 routine.release:运行时释放指定模块.
// 只从指定节点的 holders 队列移除 rid(不全量).stop 时未释放的由 runRemote defer
// Release 全清.硬约束:本 routine 必须 Started.
// 成功后从 cmd.Modules 移除(跟 holders 队列同步).
func (m *Manager) OnRelease(id int, modules []string) error {
	if err := m.requireSelfRunning(id, "release"); err != nil {
		return err
	}
	m.tree.ReleaseModules(id, modules)
	m.mu.Lock()
	n := m.nodes[id]
	m.mu.Unlock()
	if n != nil {
		n.cmd.RemoveModules(modules...)
	}
	return nil
}

// OnForceRelease 处理 python 的 routine.force_release:强制释放 modules(只驱逐,不占).
// 流程:算 cone 内第三方 holder -> cascade stop 驱逐(forceBy=rid,透传 reason='force').
// 驱逐完即返回 ok--不自己 TryAcquire,模块空出由调用方另行 acquire/force_acquire.
// 跟 OnForceAcquire 的区别:force_acquire 驱逐后自己占住(带驱逐的 acquire,原子无竞态);
// force_release 只清场,驱逐与后续 acquire 间有竞态窗口(调用方自担).
//
// 硬约束:rid 自己须 Started.永不驱逐祖先(EvictableHolders 排除 ancestors).
// 异步(reader go 调):含等被驱逐者 stopped,阻塞会死锁 reader.完成后自己发
// routine.released ack 给来源 client.
func (m *Manager) OnForceRelease(rid int, modules []string, reqID, sourceClientID string) {
	reply := func(ok bool, errMsg string) {
		msg := map[string]any{"event": conn.RoutineReleased, "req_id": reqID}
		if ok {
			msg["ok"] = true
		} else {
			msg["ok"] = false
			msg["error"] = errMsg
		}
		m.sendOut(sourceClientID, msg)
	}
	if err := m.requireSelfRunning(rid, "force_release"); err != nil {
		reply(false, err.Error())
		return
	}
	m.mu.Lock()
	n := m.nodes[rid]
	ancestorIDs := ancestorIDSet(n)
	m.mu.Unlock()

	holders := m.tree.EvictableHolders(modules, ancestorIDs, rid)
	for _, h := range holders {
		m.mu.Lock()
		hn := m.nodes[h]
		m.mu.Unlock()
		if hn == nil {
			continue // 已不在(竞态:刚自己停了)
		}
		m.log.Infof("🛑 stop %s (force, evicting for %s)", hn.cmd, n.cmd)
		m.stop(hn, true, rid) // cascade:连子树一起停(子可能也持 cone 内模块)
	}
	m.log.Infof("⚡ force_release %s ← %v (evicted %d, not acquired)", n.cmd, modules, len(holders))
	reply(true, "")
}

// OnForceAcquire 处理 python 的 routine.force_acquire:强制占领 modules(驱逐+占住).
// 流程:算 cone 内第三方 holder -> cascade stop 驱逐 -> rid 自己 TryAcquire 占住.
// 单轮驱逐,不重试--驱逐后仍冲突(竞态)返回 error.ack 走 routine.acquired
// (跟 acquire 同--force_acquire 是带驱逐的 acquire).
//
// 硬约束:rid 自己须 Started.永不驱逐祖先(EvictableHolders 排除 ancestors,root
// 是所有 routine 的祖先故永不动).
// 异步(reader go 调):含等被驱逐者 stopped,阻塞会死锁 reader.完成后自己发
// routine.acquired ack 给来源 client.
func (m *Manager) OnForceAcquire(rid int, modules []string, reqID, sourceClientID string) {
	reply := func(ok bool, errMsg string) {
		msg := map[string]any{"event": conn.RoutineAcquired, "req_id": reqID}
		if ok {
			msg["ok"] = true
		} else {
			msg["ok"] = false
			msg["error"] = errMsg
		}
		m.sendOut(sourceClientID, msg)
	}
	if err := m.requireSelfRunning(rid, "force_acquire"); err != nil {
		reply(false, err.Error())
		return
	}
	m.mu.Lock()
	n := m.nodes[rid]
	ancestorIDs := ancestorIDSet(n)
	m.mu.Unlock()

	holders := m.tree.EvictableHolders(modules, ancestorIDs, rid)
	for _, h := range holders {
		m.mu.Lock()
		hn := m.nodes[h]
		m.mu.Unlock()
		if hn == nil {
			continue // 已不在(竞态:刚自己停了)
		}
		m.log.Infof("🛑 stop %s (force, evicting for %s)", hn.cmd, n.cmd)
		m.stop(hn, true, rid) // cascade:连子树一起停(子可能也持 cone 内模块)
	}

	// 驱逐完 rid 自己占.TryAcquire 原子(check+occupy 同锁),失败则啥都没占.
	if err := m.tree.TryAcquire(rid, modules, ancestorIDs); err != nil {
		var ce *module.ConflictError
		if errors.As(err, &ce) {
			// 仍冲突:驱逐期间被别人抢了(竞态).单轮不重试--返回 error 让调用方决策.
			m.mu.Lock()
			holderNames := m.holderNameStrings(ce.Holders)
			m.mu.Unlock()
			reply(false, fmt.Sprintf(
				"module %q still held by %s after force eviction",
				ce.Requested, holderNames))
			return
		}
		reply(false, err.Error())
		return
	}
	m.mu.Lock()
	n.cmd.AddModules(modules...)
	m.mu.Unlock()
	m.log.Infof("⚡ force_acquire %s ← %v", n.cmd, modules)
	reply(true, "")
}

// OnForceStart 处理 python 的 routine.force_start:抢占式 start 子.
// 流程:requireParentRunning → 算子 declared 模块的 cone 内第三方 holder →
// cascade stop 驱逐(forceBy=childID)→ m.Start(TryAcquire + cmd.Start).
// 成功走正常 started 路径(lifecycle.started relayed → py child_ack resolve None);
// 失败(驱逐后仍冲突)发 rejected{op:force_start} 给来源 client(child_ack resolve err).
// 异步(reader go 调):含等被驱逐者 stopped,阻塞会死锁 reader.
func (m *Manager) OnForceStart(childID int, sourceClientID string) {
	reject := func(errMsg string) {
		m.sendOut(sourceClientID, map[string]any{
			"event": conn.RoutineRejected, "op": "force_start",
			"child_id": strconv.Itoa(childID), "error": errMsg,
		})
	}
	if err := m.requireParentRunning(childID, "force_start"); err != nil {
		reject(err.Error())
		return
	}
	m.mu.Lock()
	child := m.nodes[childID]
	declared := child.declared
	ancestorIDs := ancestorIDSet(child)
	m.mu.Unlock()

	m.log.Infof("⚡ force_start %s", child.cmd)
	holders := m.tree.EvictableHolders(declared, ancestorIDs, childID)
	for _, h := range holders {
		m.mu.Lock()
		hn := m.nodes[h]
		m.mu.Unlock()
		if hn == nil {
			continue
		}
		m.log.Infof("🛑 stop %s (force, evicting for %s)", hn.cmd, child.cmd)
		m.stop(hn, true, childID)
	}

	// m.Start = TryAcquire(declared) + cmd.Start(→ runRemote lifecycle.created/start).
	// 成功后 lifecycle.started 回来由 reader relay(child_ack 在 py 侧 resolve None).
	if err := m.Start(childID, nil); err != nil {
		reject(err.Error())
	}
}

// holderNameStrings 把 holder rid 列表转成 "name-id" 字符串列表(错误信息用).
func (m *Manager) holderNameStrings(holders []int) string {
	parts := make([]string, 0, len(holders))
	for _, h := range holders {
		if hn, ok := m.nodes[h]; ok {
			parts = append(parts, hn.cmd.String())
		} else {
			parts = append(parts, strconv.Itoa(h))
		}
	}
	return strings.Join(parts, ", ")
}

// requireSelfRunning 校验 id 这条 routine 自己处于 Started(starting/running)态.
// acquire/release 只允许 start 期间;ready(created)/ stopped 阶段调返回 error.
// 错误带 name#id----rejected 回执自包含 routine 标识,Python 不用反查.
// 跟 start/stop 子的 requireParentRunning 区别:那个查父,这个查自己.
func (m *Manager) requireSelfRunning(id int, op string) error {
	m.mu.Lock()
	n, ok := m.nodes[id]
	m.mu.Unlock()
	if !ok {
		return fmt.Errorf("routine %d not found", id)
	}
	if st := n.cmd.State(); st != command.StateStarting && st != command.StateRunning {
		return fmt.Errorf("cannot %s: routine %s not started (state=%s)",
			op, n.cmd, st)
	}
	return nil
}

// requireParentRunning 校验 childID 的父 routine 是否处于 Running 态.
// submit 在父 created 后即可(父 StateCreated 也行),但 start/stop 子需父 Started.
// 根 routine(无父)由 Go 侧 Execute/Start 直接驱动,不经此路径.
// 错误带 name#id----parent not started 带父的 name#id.
func (m *Manager) requireParentRunning(childID int, op string) error {
	m.mu.Lock()
	n, ok := m.nodes[childID]
	m.mu.Unlock()
	if !ok {
		return fmt.Errorf("routine %d not found", childID)
	}
	if n.parent == nil {
		return nil // 根 routine,由 Go 侧直接驱动
	}
	if st := n.parent.cmd.State(); st != command.StateRunning {
		return fmt.Errorf("cannot %s routine %s: parent %s not started (state=%s)",
			op, n.cmd, n.parent.cmd, st)
	}
	return nil
}

// --- 公开 API(go 侧业务调用 start/stop,或测试直接用) ---

// StartChild start 子命令(占模块+运行).kwargs(routine 入参)存入 cmd.Kwargs,
// runRemote 经 lifecycle.start 投递给 routine.start().通常不传(沿用 submit 时
// 存的);保留参数给 Go 侧直接驱动场景覆盖用.
func (m *Manager) StartChild(childID int, kwargs map[string]any) error {
	return m.Start(childID, kwargs)
}

// StopChild 级联停子命令.
func (m *Manager) StopChild(childID int) { m.Stop(childID) }

// --- 入站事件 dispatch(经 bus 触发,从 rpc reader 搬来)---
//
// dispatchEvent 处理一条入站事件 msg(来自 connID 这条 conn).reader 纯 publish,
// 所有事件都进来----包括 future 回执(created/started/stopped),由本函数 resolve
// node 上的 future chan(runRemote/Execute 的 sync 等待走它们).业务 event 的分发
// 也在此.future resolve 非阻塞(cap1 + select default):回报 / outfail / failPending
// 三源都可能 resolve 同一 chan,先到先得,其余跳过.
//
// LifecycleStopped created-failure 分流:若 routine 还在 created 态
// (cmd.State==StateCreated,started 没发过),stopped 是 created 失败----resolve
// createdCh with err(让 created-phase waiter 退),跳过 OnRoutineTerminated/Relay
// (caller 清理,不级联,不 relay).取代旧的"reader popCreatedWaiter 命中则不
// publish"----改成 publish 了但 Manager 按 cmd.State 判定.
func (m *Manager) dispatchEvent(connID string, msg map[string]any) {
	id, _ := msg["id"].(string)
	switch conn.Event(msg) {
	case conn.LifecycleCreated:
		// created 回报:resolve createdCh(带 modules).modules 经 chan 流转,waiter
		//(runRemote/Execute)直接拿存进 n.declared----去掉 OnCreatedModules 回调.
		m.resolveCreated(id, msg)
	case conn.LifecycleStarted:
		// resolve startedCh + 中转回 python 父 handle(唤醒 wait_started).
		m.resolveStarted(id)
		m.OnRelayToParent(conn.ToInt(id), msg)
	case conn.LifecycleStopped:
		// 正常 stopped:resolve stoppedCh(runRemote 等待)+ 自动退订 + 级联终止 + relay 父.
		// created 失败(cmd.State==StateCreated):resolve createdCh with err,跳过级联/relay.
		m.resolveStopped(id, msg)
	case conn.PubsubSubscribe:
		m.OnPubsubSubscribe(str(msg, "topic"), str(msg, "namespace"), str(msg, "id"))
	case conn.PubsubUnsubscribe:
		m.OnPubsubUnsubscribe(str(msg, "topic"), str(msg, "namespace"), str(msg, "id"))
	case conn.PubsubPublish:
		m.OnPubsubPublish(str(msg, "topic"), str(msg, "namespace"), str(msg, "source_id"), msg["data"])
	case conn.RoutineYield:
		isFinal, _ := msg["is_final"].(bool)
		errMsg, _ := msg["error"].(string)
		m.OnRoutineYield(id, msg["data"], isFinal, errMsg)
	case conn.RoutineSubmit:
		m.handleReverseSubmit(connID, msg)
	case conn.RoutineStart:
		childID := conn.ToInt(msg["child_id"])
		if childID > 0 {
			try, _ := msg["try"].(bool)
			if err := m.OnStartChild(childID, try); err != nil {
				rej := map[string]any{
					"event": conn.RoutineRejected, "op": "start",
					"child_id": strconv.Itoa(childID), "error": err.Error(),
				}
				if try {
					rej["try"] = true
				}
				m.sendConn(connID, rej)
			}
		}
	case conn.RoutineStop:
		childID := conn.ToInt(msg["child_id"])
		if childID > 0 {
			if err := m.OnStopChild(childID); err != nil {
				m.sendConn(connID, map[string]any{
					"event": conn.RoutineRejected, "op": "stop",
					"child_id": strconv.Itoa(childID), "error": err.Error(),
				})
			}
		}
	case conn.RoutineUnsubmit:
		childID := conn.ToInt(msg["child_id"])
		if childID > 0 {
			if err := m.OnUnsubmitChild(childID); err != nil {
				m.sendConn(connID, map[string]any{
					"event": conn.RoutineRejected, "op": "unsubmit",
					"child_id": strconv.Itoa(childID), "error": err.Error(),
				})
			}
		}
	case conn.RoutineAcquire:
		reqID, _ := msg["req_id"].(string)
		rid := conn.ToInt(msg["id"])
		modules := conn.ToStringSlice(msg["modules"])
		if rid > 0 {
			err := m.OnAcquire(rid, modules)
			reply := map[string]any{"event": conn.RoutineAcquired, "req_id": reqID}
			if err != nil {
				reply["ok"] = false
				reply["error"] = err.Error()
			} else {
				reply["ok"] = true
			}
			m.sendConn(connID, reply)
		}
	case conn.RoutineRelease:
		reqID, _ := msg["req_id"].(string)
		rid := conn.ToInt(msg["id"])
		modules := conn.ToStringSlice(msg["modules"])
		if rid > 0 {
			err := m.OnRelease(rid, modules)
			reply := map[string]any{"event": conn.RoutineReleased, "req_id": reqID}
			if err != nil {
				reply["ok"] = false
				reply["error"] = err.Error()
			} else {
				reply["ok"] = true
			}
			m.sendConn(connID, reply)
		}
	case conn.RoutineForceRelease:
		// 异步(含驱逐等 stopped,阻塞会死锁 dispatch goroutine).
		// 完成后 OnForceRelease 自己发 released ack 给来源 conn.
		reqID, _ := msg["req_id"].(string)
		rid := conn.ToInt(msg["id"])
		modules := conn.ToStringSlice(msg["modules"])
		if rid > 0 {
			go m.OnForceRelease(rid, modules, reqID, connID)
		}
	case conn.RoutineForceAcquire:
		// 异步(含驱逐等 stopped).完成后 OnForceAcquire 自己发 acquired ack
		// (驱逐+占住,带驱逐的 acquire).对标 force_release 的 dispatch.
		reqID, _ := msg["req_id"].(string)
		rid := conn.ToInt(msg["id"])
		modules := conn.ToStringSlice(msg["modules"])
		if rid > 0 {
			go m.OnForceAcquire(rid, modules, reqID, connID)
		}
	case conn.RoutineForceStart:
		// 异步(驱逐含等 stopped).
		childID := conn.ToInt(msg["child_id"])
		if childID > 0 {
			go m.OnForceStart(childID, connID)
		}
	case conn.RoutineGetRunning:
		// dial-out routine 经 Stream 查 running 实例(dial-in 走 Req 不到此).
		// 对标 submit/submitted:带 req_id,kernel 回 get_running_reply.
		reqID, _ := msg["req_id"].(string)
		m.sendConn(connID, map[string]any{
			"event":    conn.RoutineGetRunningReply,
			"req_id":   reqID,
			"routines": m.RunningRoutines(),
		})
	case conn.RoutineGetModuleTree:
		// dial-out routine 经 Stream 拉 module.tree(dial-in 走 Req 不到此).
		// 对标 get_running:带 req_id,kernel 回 get_module_tree_reply.
		reqID, _ := msg["req_id"].(string)
		tree := module.Default()
		reply := map[string]any{
			"event":  conn.RoutineGetModuleTreeReply,
			"req_id": reqID,
		}
		if tree == nil {
			reply["ok"] = false
			reply["error"] = "module tree not initialized"
		} else {
			reply["ok"] = true
			reply["tree"] = tree.Serialize()
		}
		m.sendConn(connID, reply)
	case conn.RoutineLoadModule:
		// py->kernel: 往父模块加载子模块(全局树动态增).带 req_id 回执,对标 acquire.
		// 只挂树不占用--占用另调 TryAcquire/acquire.成功后重推 module.tree 给所有 conn.
		reqID, _ := msg["req_id"].(string)
		parentID, _ := msg["parent_id"].(string)
		childID, _ := msg["child_id"].(string)
		name, _ := msg["name"].(string)
		err := module.Default().LoadModule(parentID, childID, name)
		reply := map[string]any{"event": conn.RoutineModuleLoaded, "req_id": reqID}
		if err != nil {
			reply["ok"] = false
			reply["error"] = err.Error()
		} else {
			reply["ok"] = true
		}
		m.sendConn(connID, reply)
		if err == nil {
			m.pushModuleView("") // 全局树变了,重推给所有 conn(含发起者)
		}
	case conn.RoutineUnloadModule:
		// py->kernel: 卸载子模块(全局树动态删).带 req_id 回执.成功后重推 module.tree.
		reqID, _ := msg["req_id"].(string)
		childID, _ := msg["child_id"].(string)
		err := module.Default().UnloadModule(childID)
		reply := map[string]any{"event": conn.RoutineModuleUnloaded, "req_id": reqID}
		if err != nil {
			reply["ok"] = false
			reply["error"] = err.Error()
		} else {
			reply["ok"] = true
		}
		m.sendConn(connID, reply)
		if err == nil {
			m.pushModuleView("")
		}
	case conn.RoutineRenameModule:
		// py->kernel: 重命名模块(仅改 Name,ID/拓扑不变).带 req_id 回执.成功后重推 module.tree.
		reqID, _ := msg["req_id"].(string)
		id, _ := msg["id"].(string)
		newName, _ := msg["new_name"].(string)
		err := module.Default().RenameModule(id, newName)
		reply := map[string]any{"event": conn.RoutineModuleRenamed, "req_id": reqID}
		if err != nil {
			reply["ok"] = false
			reply["error"] = err.Error()
		} else {
			reply["ok"] = true
		}
		m.sendConn(connID, reply)
		if err == nil {
			m.pushModuleView("")
		}
	case conn.RoutineMoveModule:
		// py->kernel: 移动模块到新父下(改 ParentID+父子 Children).带 req_id 回执.
		// 成功后重推 module.tree.
		reqID, _ := msg["req_id"].(string)
		id, _ := msg["id"].(string)
		newParentID, _ := msg["new_parent_id"].(string)
		err := module.Default().MoveModule(id, newParentID)
		reply := map[string]any{"event": conn.RoutineModuleMoved, "req_id": reqID}
		if err != nil {
			reply["ok"] = false
			reply["error"] = err.Error()
		} else {
			reply["ok"] = true
		}
		m.sendConn(connID, reply)
		if err == nil {
			m.pushModuleView("")
		}
	case conn.MessageSend:
		targets, _ := msg["target_ids"].([]any)
		m.OnMessage(conn.ToStringSlice(targets), str(msg, "source_id"), conn.MessageDelivered, msg["data"])
	case conn.MessageReq:
		targets, _ := msg["target_ids"].([]any)
		m.OnMessage(conn.ToStringSlice(targets), str(msg, "source_id"), conn.MessageReqDelivered, msg["data"])
	case conn.MessageReqReply:
		targets, _ := msg["target_ids"].([]any)
		m.OnMessage(conn.ToStringSlice(targets), str(msg, "source_id"), conn.MessageReqReplyDelivered, msg["data"])
	case conn.MessageStreamOpen:
		targets, _ := msg["target_ids"].([]any)
		m.OnMessage(conn.ToStringSlice(targets), str(msg, "source_id"), conn.MessageStreamOpenDelivered, msg["data"])
	case conn.MessageStreamData:
		targets, _ := msg["target_ids"].([]any)
		m.OnMessage(conn.ToStringSlice(targets), str(msg, "source_id"), conn.MessageStreamDataDelivered, msg["data"])
	case conn.MessageStreamCancel:
		targets, _ := msg["target_ids"].([]any)
		m.OnMessage(conn.ToStringSlice(targets), str(msg, "source_id"), conn.MessageStreamCancelDelivered, msg["data"])
	case conn.CatalogPush:
		// dial-in routine 主动 push catalog → 注册路由 + 推 module.tree + 起 passive.
		m.handleCatalogPush(connID, msg)
	case conn.CatalogRegister:
		// 运行时 register_routine 单条增量注册(同名 fail).
		m.handleCatalogRegister(connID, msg)
	case conn.CatalogReload:
		// 运行时 reload_routine 单条重载(不区分 conn 覆盖).
		m.handleCatalogReload(connID, msg)
	case conn.CatalogDeregister:
		// 运行时 deregister_routine 单条移除(两跳:kernel→持有者→kernel→请求者).
		m.handleCatalogDeregister(connID, msg)
	case conn.CatalogDeregisterCmdAck:
		// 持有者 hub 本地 dereg 后的回执,kernel 据此删路由 + 回执请求者.
		m.handleCatalogDeregisterCmdAck(connID, msg)
	}
}

// str 取 msg[key] 的 string 值(缺失返回空串).
func str(msg map[string]any, key string) string {
	s, _ := msg[key].(string)
	return s
}

// sendConn 发消息到 connID 这条 conn(回执 / rejected / ack).找不到 conn 静默丢弃.
func (m *Manager) sendConn(connID string, msg map[string]any) {
	m.sendOut(connID, msg)
}

// --- 出站(经 bus:上层 publish 到 conn.out.<connID>,该 conn 的 sendLoop 发送)---
//
// 出站全走 bus:跟入站对称(一个心智模型)+ tracer 能看到出站命令.fire-and-forget
// (broker 转发 / ack / destroy)只 publish;sync lifecycle(created/start)publish 后
// 调用方 select 等 node chan----send 失败经 sendLoop→conn.outfail→Manager resolve chan
// with err 回流,不丢,调用方拿得到错.

// sendOut publish 一条出站 msg 到 connID 的 sendLoop.conn 不存在/已断 = 无订阅者 =
// bus 丢弃(fire-and-forget 语义:断了就丢).lifecycle 命令的 send 失败由 sendLoop
// 自己 publish outfail 回流.
func (m *Manager) sendOut(connID string, msg map[string]any) {
	bus.GetBus().Publish(conn.TopicOut+"."+connID, conn.OutMsg{Msg: msg})
}

// sendCreated 发 lifecycle.created(实例化+注册+激活通信).kwargs 投给 created().
// parentID 非空时带上,让子 routine 知道是谁 submit 的自己.
func (m *Manager) sendCreated(connID, id, name string, kwargs map[string]any, parentID string) {
	msg := map[string]any{"event": conn.LifecycleCreated, "id": id, "name": name}
	if kwargs != nil {
		msg["kwargs"] = kwargs
	}
	if parentID != "" && parentID != "0" {
		msg["parent_id"] = parentID
	}
	m.sendOut(connID, msg)
}

// sendStart 发 lifecycle.start.kwargs 不带----py 侧 start() 用 created 时存的 _init_kwargs.
func (m *Manager) sendStart(connID, id, name string) {
	m.sendOut(connID, map[string]any{"event": conn.LifecycleStart, "id": id, "name": name})
}

// sendStop 发 lifecycle.stop.reason 非空带("force"=驱逐)+ by(evictor rid).
func (m *Manager) sendStop(connID, id, reason, by string) {
	msg := map[string]any{"event": conn.LifecycleStop, "id": id}
	if reason != "" {
		msg["reason"] = reason
		if by != "" {
			msg["by"] = by
		}
	}
	m.sendOut(connID, msg)
}

// sendDestroy 发 lifecycle.destroy(销毁 created 态 routine,无 body).
func (m *Manager) sendDestroy(connID, id string) {
	m.sendOut(connID, map[string]any{"event": conn.LifecycleDestroy, "id": id})
}

// --- future chan resolve(dispatchEvent 调,非阻塞:cap1 + select default)---
// 三源(回报 / outfail / failPending)都可能 resolve,先到先得,其余跳过.

// resolveCreated 把 created 回报填进 node.createdCh(带 modules),并标记 createdDone
// (resolveStopped 据此判 created 失败).
func (m *Manager) resolveCreated(id string, msg map[string]any) {
	n := m.nodeByID(id)
	if n == nil {
		return
	}
	var mods []string
	if ma, ok := msg["modules"].([]any); ok {
		mods = conn.ToStringSlice(ma)
	}
	m.mu.Lock()
	n.createdDone = true
	m.mu.Unlock()
	select {
	case n.createdCh <- conn.CreatedResult{Modules: mods}:
	default:
	}
}

// resolveStarted close-equivalent:send struct{}{} 进 node.startedCh(cap1,幂等).
func (m *Manager) resolveStarted(id string) {
	n := m.nodeByID(id)
	if n == nil {
		return
	}
	select {
	case n.startedCh <- struct{}{}:
	default:
	}
}

// resolveStopped 处理 lifecycle.stopped:created 失败(!createdDone,created 还没回报)
// → resolve createdCh with err(跳过级联/relay,caller 清理);正常 → resolve stoppedCh
// + OnRoutineStopped(退订)+ OnRoutineTerminated(级联清理)+ OnRelayToParent.
func (m *Manager) resolveStopped(id string, msg map[string]any) {
	rid := conn.ToInt(id)
	n := m.nodeByID(id)
	// created 失败判定:node 在 + created 还没 resolve(!createdDone).
	if n != nil {
		m.mu.Lock()
		createdFail := !n.createdDone
		if createdFail {
			n.createdDone = true
		}
		m.mu.Unlock()
		if createdFail {
			reason, _ := msg["reason"].(string)
			select {
			case n.createdCh <- conn.CreatedResult{Err: fmt.Errorf(
				"routine #%s stopped during created: reason=%s", id, reason)}:
			default:
			}
			// created 阶段已 auto_subscribe,退订正确;但不级联(无 body 无子),不 relay.
			m.OnRoutineStopped(id)
			return
		}
		// 正常 stopped:resolve stoppedCh.
		select {
		case n.stoppedCh <- msg:
		default:
		}
	}
	m.OnRoutineStopped(id)
	m.OnRoutineTerminated(rid)
	m.OnRelayToParent(rid, msg)
}

// nodeByID 按 string id 查 node(持锁).
func (m *Manager) nodeByID(idStr string) *node {
	rid := conn.ToInt(idStr)
	if rid == 0 {
		return nil
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.nodes[rid]
}

// handleReverseSubmit 处理 python 发来的 routine.submit(来自 connID 这条 conn):
// 建 created 态命令 → 用子 routine 所属的 conn 发 lifecycle.created(跨 server:子的 conn
// 可能跟 submit 来源不同)→ 等 created 回报(带 modules)→ 发 routine.submitted 回执给
// 来源 conn(python 在那等).
//
// 这样 py 侧 submit 拿到 child_id 时 instance 一定已 created,无需 wait_created.
// 等待在独立 goroutine----dispatchEvent 在 dispatch goroutine 里被调,阻塞会死锁
// (created 回报要经 reader→bus→dispatch 投递).
func (m *Manager) handleReverseSubmit(connID string, msg map[string]any) {
	reqID, _ := msg["req_id"].(string)
	name, _ := msg["name"].(string)
	kwargs, _ := msg["kwargs"].(map[string]any)
	parentID := conn.ToInt(msg["parent_id"])
	// modules 不从 wire 取----对标老版 push_quick 只发 name+kwargs.modules 由 created
	// 回报回带(created() 返回值),经 CreatedResult chan 流转.

	childID, err := m.OnSubmit(parentID, name, kwargs)
	if err != nil {
		m.OnSubmitSubmitted(connID, reqID, 0, nil, err)
		return
	}

	// 用子 routine 所属的 conn 发 created(跨 server 正确路由)+ 注册 created waiter.
	createdCh, err := m.OnSubmitCreated(childID, name, kwargs, parentID)
	if err != nil {
		m.OnSubmitSubmitted(connID, reqID, 0, nil, err)
		return
	}

	go func() {
		var submitErr error
		var mods []string
		select {
		case r := <-createdCh:
			submitErr = r.Err
			mods = r.Modules
		case <-time.After(conn.CreatedTimeout):
			submitErr = fmt.Errorf("routine %s created timeout after %s", name, conn.CreatedTimeout)
			m.OnSubmitSubmitted(connID, reqID, childID, nil, submitErr)
			return
		}
		// submitted 回执发回 submit 来源 conn(connID)----python 在那等.
		// 带 modules(created 回报里的 created() 返回值)----父 handle 据此算冲突.
		m.OnSubmitSubmitted(connID, reqID, childID, mods, submitErr)
	}()
}
