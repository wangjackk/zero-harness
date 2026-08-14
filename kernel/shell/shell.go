// Package shell kernel 核心的编排层:routine 树管理(Create / Start / Execute / Stop 级联).
// 知道父子关系,算 effective,级联 Stop.
// 之下是 kernel/command(执行单元)+ kernel/module(冲突校验),二者对父子关系一无所知.
// 之上通过 client 代理 gRPC 驱动远端 routine server(routine 体不在本地).
//
// 契约:Stop 必须走这里(级联),不能绕过直接 command.Stop(父)----
// 否则搭车模式下子会变无 tag 悬空.
//
// 本包按职责拆成多个同包文件:
//   - shell.go(本文件):Manager 类型 + root + Create/Start/Execute/Stop + 树辅助
//   - bus 订阅(dispatch 入站事件 + conn 生命周期)
//   - handler.go:入站事件 dispatch(从 grpc reader 搬来)+ 反向 submit/start/stop/acquire
//   - broker.go:broker 中央化转发(pubsub/message/body)
//   - remote.go:runRemote 远端 lifecycle 状态机
//   - passive.go:passive 自动拉起 + 断线卸载
package shell

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"sync"

	"kernel/bus"
	"kernel/command"
	"kernel/conn"
	"kernel/logger"
	"kernel/module"
)

// node routine 树节点:包装 command + 父子链 + 声明模块 + client 归属 + future chan.
//
// future chan(created/started/stopped 回执)在 node 上,不在 conn----routine 实例级
// 状态归 routine 自己.reader 纯 publish 到 bus,Manager dispatchEvent 收到回报时
// resolve 这些 chan(runRemote / Execute 的 sync 等待走它们).chan 在 Create 时建好,
// 先于任何 send----无注册竞态(比旧的 register-then-send 更简单).
//
// cap1 + 非阻塞 resolve:conn.down 的 failPending / outfail / 回报 三个源都可能 resolve
// 同一 chan,非阻塞 send(select default)保证只填第一个,其余跳过----无重复,无阻塞.
type node struct {
	cmd      *command.Command
	parent   *node
	children []*node
	declared []string // 声明模块;Start 时才算 effective
	clientID string   // 这条 routine 由哪个 client 驱动(root=""本地骨架)
	// future chan:created/started/stopped 回执.Manager dispatchEvent + outfailLoop +
	// connLifeline down failPending 三处 resolve(非阻塞,先到先得).
	createdCh chan conn.CreatedResult // cap1:created 回报(带 modules)/ created 失败 / outfail(created) / conn.down
	startedCh chan struct{}           // cap1:started 回报(send struct{}{})/ outfail(start) 不碰(走 stoppedCh)
	stoppedCh chan map[string]any     // cap1:stopped 回报 / outfail(start/stop) / conn.down
	// createdDone:created 回报已 resolve(成功或失败).resolveStopped 据此判 created 失败
	//(stopped 在 created 之前到 = !createdDone)----比 cmd.State 准(Start 已把 state 设
	// 成 Starting,StateCreated 不再可靠).只在 dispatchLoop 里 resolveCreated/resolveStopped
	// 读写,单 goroutine 无竞态;failPending/outfail 不碰它(只非阻塞 send chan).
	createdDone bool
	// createdSent:submit 路径 OnSubmitCreated 已发 lifecycle.created 并等过回报.
	// runRemote 据此跳过 created 阶段----否则重复发 created + registered 的 created
	// 无人 resolve(Manager 在 submit 阶段已 resolve)= 泄漏.Execute 直启 false.
	createdSent bool
	// stopping:stop(n) 已接管(幂等守卫).显式 stop(OnStopChild/Stop)与自然终止
	//(OnRoutineTerminated)可能并发调 stop(n),后者看到 stopping=true 直接返回,
	// 避免重复级联 + 重复日志.
	stopping bool
	// stopReason / stopBy:force 驱逐时设(reason="force",by=evictor rid).
	// runRemote 发 lifecycle.stop 时带上,让被驱逐的 routine 在 on_done 收到
	// reason='force'(做紧急退让而非 graceful 收尾).零值 = 正常 stop(不带 reason 字段).
	stopReason string
	stopBy     int
}

// routineRoute 是 routine name → clientID 的路由表项.
// isPassive 标记该 routine 是否被动启动(kernel 自动 start)----passive 不允许
// 业务侧手动 submit(OnSubmit 拦截),只走 AutoStartPassive / Execute / Create
// 这些 internal 路径.从 catalog.register / catalog.reload / catalog.push 的
// is_passive{flag, kwargs} 拆解而来(parsePassive)----passiveKwargs 是 auto-start
// 的默认入参(routines.yaml 条目 kwargs,py 侧 merge 进 is_passive dict 声明),
// AutoStartPassive 直接 Execute(name, passiveKwargs),run(kwargs) 自然收到.
type routineRoute struct {
	clientID      string
	isPassive     bool
	passiveKwargs map[string]any
	meta          map[string]any
}

// pendingDeregister 记录 catalog.deregister 两跳流程的 pending 状态.
// kernel 收到 catalog.deregister{req_id, name} 后,查路由找持有者 connID,
// 发 catalog.deregister.cmd{req_id, name} 给持有者,记此 pending;
// 收到 catalog.deregister.cmd.ack{req_id, ok} 后,据 pending 删路由 + 回执请求者.
// 请求者 == 持有者时也走此流程(cmd 发回请求者自己),保持流程统一.
type pendingDeregister struct {
	requesterConnID string // 发起 catalog.deregister 的 conn(回执目标)
	holderConnID    string // 持有该 routine 的 conn(cmd 目标,删路由时校验)
	name            string // 被 dereg 的 routine name
}

// Manager kernel 核心的编排层:routine 树管理(Create / Start / Execute / Stop 级联).
// 支持多 routine server:每个 server 一个 client(id 索引),routine 节点挂 clientID,
// 断线时按 clientID 精准卸载该 server 名下的 routine(其它 server 不受影响).
//
// 跨 server:routine 按 name 路由(routineClients 表)----submit 时按 name 查表定 clientID,
// 不继承父.所以 a@serverA submit b@serverB 能跨 server.broker 通信(req/message/pubsub/yield)
// 也在 Manager 中央化,按 target_id/subscriber_id 跨 client 路由(见 broker.go).
type Manager struct {
	tree  *module.Tree
	conns map[string]conn.Conn // id → conn(方向无关,dial-out Client / dial-in ServerConn)
	mu    sync.Mutex
	nodes map[int]*node
	// routineClients:routine name → {clientID, modules} 全局路由表.catalog 拉取时
	// 注册(每个 conn 连上把自己的 routines upsert 进去,同名 warn 覆盖,对标老版
	// router.routines[name].ClientId).submit 按 name 查表定 clientID,不继承父----
	// routine 归属由 name 决定(定义在哪个 server),不由父 routine 决定.
	// modules 一起存:跨 server submit 时本 server 没有子 routine 类,python 传空
	// modules,kernel 用 catalog 存的补上(kernel 有全局视图).
	routineClients map[string]routineRoute
	// hubIDs:connID → hub_id(进程级稳定身份,如 "zero"/"one").catalog.push /
	// get_routines 响应里带来,kernel 校验唯一性(重复则拒绝连接,Close 这条 conn).
	// list_routines 用 hub_id 标识归属,不再暴露内部 conn_id.
	hubIDs map[string]string
	// pendingDeregisters:catalog.deregister 两跳流程的 pending 表.
	// req_id → {requesterConnID, holderConnID, name}.kernel 收到 catalog.deregister
	// 后发 catalog.deregister.cmd 给持有者,记 pending;收到 cmd.ack 后删路由 + 回执请求者.
	// 请求者 == 持有者时也走此流程(cmd 发回请求者自己),保持流程统一.
	pendingDeregisters map[string]pendingDeregister
	// root 是本地调度骨架 routine:占根模块,passive/业务 routine 挂它下面.
	// 不发 lifecycle 给 server(Run 阻塞 ctx.Done(),不调 runRemote),直接 MarkRunning.
	// root 的 clientID=""(本地,不对应任何远端 server).
	root           *command.Command
	passiveStarted map[string]bool // key = clientID + "\x00" + name,按 client 隔离去重
	// pubsub 订阅表(中央化):key = namespace + "\x00" + topic → set of subscriber_id.
	// routine stopped 时清该 id 在所有 topic 的订阅(自动退订,对标老版 Command 销毁即清订阅).
	// subscriber_id 是 routine 的 command id(string),查 nodes 能得 clientID----跨 client 路由.
	pubsub map[string]map[string]struct{}
	// bus 订阅.events dispatch 入站事件(含 future 回执 resolve);connLifeline 处理
	// conn up/down(down 含 failPending + UnloadRemote);outfail 处理出站 send 失败
	// (resolve future chan with err,错误经 bus 回流).
	eventsSub    *bus.Subscriber
	connLifeline *bus.Subscriber
	outfailSub   *bus.Subscriber
	log          *logger.Logger
}

// New 构造编排 Manager.tree 为全局模块树.conn 不在此传入----用 AddConn 逐个
// 注册(支持多 server / 多 routine 进程).New 立即建 root routine 并占根模块,
// 并订阅 bus:入站事件经 dispatch goroutine 处理,conn up/down 经 lifeline
// goroutine FIFO 处理(保证 down 的 UnloadRemote+pushView 在下次 up 的 LoadCatalog
// 前完成,避免 stale 视图覆盖).
func New(tree *module.Tree) *Manager {
	m := &Manager{
		tree:              tree,
		conns:             map[string]conn.Conn{},
		nodes:             map[int]*node{},
		routineClients:    map[string]routineRoute{},
		hubIDs:            map[string]string{},
		pendingDeregisters: map[string]pendingDeregister{},
		passiveStarted:    map[string]bool{},
		pubsub:            map[string]map[string]struct{}{},
		log:               logger.GetLogger().Named("shell"),
	}
	m.initRoot()
	m.subscribeBus()
	return m
}

// subscribeBus 订阅 bus 的三个 topic,各起一个 goroutine 消费:
//   - TopicEvent:入站事件 dispatch(业务事件 + future 回执 created/started/stopped resolve).
//     buffer 大 + drop=false(不丢事件----丢 lifecycle.stopped = stuck routine).
//   - TopicConn:conn up/down 生命周期.单 goroutine FIFO(down 的清理在下次 up 前完成).
//     down 含 failPending(resolve 该 conn 名下 routine 的 future chan)+ UnloadRemote + pushView.
//   - TopicOutFail:出站 send 失败.resolve future chan with err(错误经 bus 回流).
func (m *Manager) subscribeBus() {
	b := bus.GetBus()
	m.eventsSub = b.Subscribe(conn.TopicEvent, 4096, false)
	go m.dispatchLoop()
	m.connLifeline = b.Subscribe(conn.TopicConn, 64, false)
	go m.connLifelineLoop()
	m.outfailSub = b.Subscribe(conn.TopicOutFail, 256, false)
	go m.outfailLoop()
}

// dispatchLoop 消费入站事件,调 dispatchEvent(业务 switch + future resolve).
func (m *Manager) dispatchLoop() {
	for payload := range m.eventsSub.Recv() {
		ev, ok := payload.(conn.EventIn)
		if !ok {
			continue
		}
		m.dispatchEvent(ev.ConnID, ev.Msg)
	}
}

// outfailLoop 消费出站 send 失败:按 ID 查 node,resolve 对应 future chan with err.
// 错误经 bus 回流----调用方 select 收到 err 返回,不丢,tracer 可见.
// 非阻塞 resolve(select default):chan 可能已被回报/ failPending resolve,跳过无妨.
func (m *Manager) outfailLoop() {
	for payload := range m.outfailSub.Recv() {
		f, ok := payload.(conn.OutFail)
		if !ok {
			continue
		}
		m.resolveOutFail(f)
	}
}

// resolveOutFail 按 outfail 的 event 决定 resolve 哪个 chan:created→createdCh(err),
// start/stop→stoppedCh(routine 等同 stopped,回报永不到).其它(destroy/broker/ack)
// 无 chan 可 resolve----sendLoop 已发尽力,丢了就丢了(fire-and-forget 语义).
func (m *Manager) resolveOutFail(f conn.OutFail) {
	rid, err := strconv.Atoi(f.ID)
	if err != nil || rid <= 0 {
		return
	}
	m.mu.Lock()
	n := m.nodes[rid]
	m.mu.Unlock()
	if n == nil {
		return
	}
	switch f.Event {
	case conn.LifecycleCreated:
		select {
		case n.createdCh <- conn.CreatedResult{Err: fmt.Errorf("send created failed: conn %s down", f.ConnID)}:
		default:
		}
	case conn.LifecycleStart, conn.LifecycleStop:
		select {
		case n.stoppedCh <- map[string]any{"reason": "send failed: conn " + f.ConnID + " down"}:
		default:
		}
	}
}

// failPending resolve 该 conn 名下所有 routine 的 future chan with err:解阻塞等在
// createdCh/stoppedCh 上的 runRemote / Execute.createdCh(err) + stoppedCh(err) 非阻塞
// 填(先到先得,回报/outfail 已填则跳过).startedCh 不碰----runRemote 总在
// {stopped, started} select,stopped 命中即退,不必动 started(避免误判 MarkRunning).
func (m *Manager) failPending(clientID string) {
	m.mu.Lock()
	var dead []*node
	for id, n := range m.nodes {
		if id == m.root.ID {
			continue
		}
		if n.clientID == clientID {
			dead = append(dead, n)
		}
	}
	m.mu.Unlock()
	for _, n := range dead {
		select {
		case n.createdCh <- conn.CreatedResult{Err: fmt.Errorf("conn %s disconnected", clientID)}:
		default:
		}
		select {
		case n.stoppedCh <- map[string]any{"reason": "conn " + clientID + " disconnected"}:
		default:
		}
	}
}

// connLifelineLoop 消费 conn up/down,FIFO 顺序处理.
// up:LoadCatalog(pull + register + pushView)+ 异步 AutoStartPassive.
// down:UnloadRemote + pushModuleView(缩小视图广播给剩余 conn).
func (m *Manager) connLifelineLoop() {
	for payload := range m.connLifeline.Recv() {
		cc, ok := payload.(conn.ConnChange)
		if !ok {
			continue
		}
		switch cc.Kind {
		case "up":
			conn := m.Conn(cc.ConnID)
			if conn == nil {
				continue
			}
			// dial-out:拉 catalog 注册路由表 + 推模块视图.dial-in:catalog 由 routine
			// 连上后主动 push(catalog.push 事件 → handleCatalogPush),此处不拉.
			if !cc.IsDialIn {
				passives := m.LoadCatalog(conn)
				if len(passives) > 0 {
					go m.AutoStartPassive(cc.ConnID, passives)
				}
			}
		case "down":
			// 先 fail future chan(解阻塞等在 createdCh/stoppedCh 上的 runRemote/Execute,
			// 让它们自退),再卸载死节点 + 推缩小视图.顺序保证:runRemote 解阻塞后
			// cmd.Stop(UnloadRemote 调)no-op;sendStop 的 publish 到已退出的 sendLoop
			// = 丢弃(断了发不出,正确).
			m.failPending(cc.ConnID)
			m.UnloadRemote(cc.ConnID)
			m.pushModuleView(cc.ConnID)
		}
	}
}

// AddConn 注册一条 conn(dial-out Client 或 dial-in ServerConn).
// conn 生命周期由 bus 驱动:连上时 reader 起 → publish TopicConn{up} → lifeline
// goroutine 拉 catalog + 起 passive;断线 → publish{down} → 卸载 + 推缩小视图.
func (m *Manager) AddConn(c conn.Conn) {
	m.mu.Lock()
	m.conns[c.ID()] = c
	m.mu.Unlock()
}

// Conn 按 id 取已注册的 conn(重连 reload 拉 catalog 用,broker 转发也用).
func (m *Manager) Conn(id string) conn.Conn {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.conns[id]
}

// RegisterRoutine 注册 routine name → clientID 路由(catalog 拉取时调).
// modules 不在此存----实例级,由 created 回报回带(catalog 注册时无实例,无 kwargs,
// 静态缓存对 dynamic 不准).Create 时 n.declared 初始空,created 回报填真值.
// isPassive 透传进路由表,供 OnSubmit 拦截手动 submit passive routine.
// passiveKwargs 是 auto-start 默认入参(routines.yaml 条目 kwargs),存路由表
// 供 AutoStartPassive Execute 带参(不再传 nil).
// meta 是 routine 类级元数据(对标 py 侧 Routine.meta dict,含 description /
// input_schema / output_schema / hidden 等),透传进路由表供 ListRoutines 返回----
// 让 dial-in 调用方(如 bridge)能拿到跨 hub 全量 routine 的完整信息渲染前端.
//
// 返回是否注册成功:**同名一律 fail**(不区分 conn----无论同 conn 还是跨 conn,
// name 已存在就拒绝,先到先得).覆盖语义走 ReloadRoutine(显式 reload,不区分 conn
// 覆盖).跨 conn 冲突由调用方(handleCatalogRegister / applyCatalog)处理:回执
// ok=false / log warn 跳过.
func (m *Manager) RegisterRoutine(name, clientID string, isPassive bool,
	passiveKwargs, meta map[string]any) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.routineClients[name]; ok {
		// 同名一律拒绝(不区分 conn).覆盖走 ReloadRoutine.
		return false
	}
	m.routineClients[name] = routineRoute{
		clientID: clientID, isPassive: isPassive,
		passiveKwargs: passiveKwargs, meta: meta,
	}
	return true
}

// ReloadRoutine 重载 routine name → clientID 路由(运行时 reload_routine 调).
// **不区分 conn,同名覆盖**(无论原归属是哪个 conn,新 reload 请求都覆盖路由).
// 对称 RegisterRoutine:register 同名 fail,reload 同名覆盖.返回 true(总是成功).
// isPassive / meta 透传进路由表(覆盖原值),供 OnSubmit 拦截 + ListRoutines 返回.
//
// 热替换:覆盖路由表前先停该 name 的所有运行实例(跨 conn----老 conn 的实例成孤儿
// 必须全停).走正统 Stop 流程递归级联停子树 + 触发 py 侧 stop() hook,统一处理
// passive/普通 routine.异步停避免阻塞 dispatch loop(见 StopRunningByName 注释).
func (m *Manager) ReloadRoutine(name, clientID string, isPassive bool,
	passiveKwargs, meta map[string]any) bool {
	m.StopRunningByName(name, "") // "" = 跨所有 conn 停
	m.mu.Lock()
	defer m.mu.Unlock()
	m.routineClients[name] = routineRoute{
		clientID: clientID, isPassive: isPassive,
		passiveKwargs: passiveKwargs, meta: meta,
	}
	return true
}

// DeregisterRoutine 移除单条 routine 路由(catalog.deregister / 断线清理调).
// 只删属于 clientID 的(同名跨 conn 不误删).返回是否真的删了.
//
// 热移除:删路由表前先停该 name 在本 conn 的运行实例(deregister 只删本 conn 路由,
// 不影响其他 conn 的同名 routine).走正统 Stop 流程递归级联停子树 + 触发 py 侧
// stop() hook,统一处理 passive/普通 routine.异步停避免阻塞 dispatch loop.
func (m *Manager) DeregisterRoutine(name, clientID string) bool {
	m.StopRunningByName(name, clientID) // 只停本 conn 的实例
	m.mu.Lock()
	defer m.mu.Unlock()
	route, ok := m.routineClients[name]
	if !ok || route.clientID != clientID {
		return false // 不存在 / 跨 conn 不动
	}
	delete(m.routineClients, name)
	return true
}

// HasRoutine 报告 name 是否已注册路由(catalog 拉取/push 注册).测试用:poll 等
// dial-in routine 的 catalog.push 到达后再 Execute.
func (m *Manager) HasRoutine(name string) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	_, ok := m.routineClients[name]
	return ok
}

// ListRoutines 返回全量路由表(catalog 注册的全部 routine,跨所有 conn).
// dial-in routine 经 get_routines Req 查询时调.每条返回
// {name, hub_id, is_passive, meta}----meta 含 description / input_schema /
// output_schema / hidden 等(对标 py 侧 Routine.meta),供 bridge 渲染前端参数表单.
// hub_id 来自 hubIDs[connID](进程级身份,如 "zero"/"one"),必填----hub_id 为空说明
// 上游(registerHubID)未通过校验,该 conn 已被拒绝,不应出现在路由表里.
func (m *Manager) ListRoutines() []any {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]any, 0, len(m.routineClients))
	for name, rt := range m.routineClients {
		out = append(out, map[string]any{
			"name":       name,
			"hub_id":     m.hubIDs[rt.clientID],
			"is_passive": map[string]any{"flag": rt.isPassive, "kwargs": rt.passiveKwargs},
			"meta":       rt.meta,
		})
	}
	return out
}

// registerHubID 注册 connID → hubID 映射,校验 hub_id 全局唯一.
// hub_id 必填(空字符串返回 false,拒绝连接);重复(已存在同名 hub_id 但归属不同 conn)
// 也返回 false----调用方应 Close 该 conn 拒绝连接.
func (m *Manager) registerHubID(connID, hubID string) bool {
	if hubID == "" {
		m.log.Errorf("⛔ hub_id 为空,拒绝连接(conn %s)", connID)
		return false
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	for cid, h := range m.hubIDs {
		if h == hubID && cid != connID {
			m.log.Errorf("⛔ hub_id %q 重复(已由 conn %s 注册,新 conn %s 拒绝连接)",
				hubID, cid, connID)
			return false
		}
	}
	m.hubIDs[connID] = hubID
	return true
}

// removeHubID 删除 connID 的 hub_id 映射(conn 断线时调).
func (m *Manager) removeHubID(connID string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.hubIDs, connID)
}

// routeOf 按 name 查路由表返回该 routine 的路由项(clientID).
// submit/Create 用它定位 client----routine 归属由 name 决定,不继承父.
func (m *Manager) routeOf(name string) routineRoute {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.routineClients[name]
}

// Conns 返回所有已注册 conn 的快照(持锁拷贝).广播模块视图等场景用----
// 持锁期间拷贝切片,释放锁后再逐个 Req,避免锁内发网络请求阻塞其他 Manager 操作.
func (m *Manager) Conns() []conn.Conn {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]conn.Conn, 0, len(m.conns))
	for _, c := range m.conns {
		out = append(out, c)
	}
	return out
}

// initRoot 建本地 root routine:占根模块,Start + MarkRunning(不经 lifecycle/start,
// 不调 runRemote).root 是调度骨架,不对应远端 routine----Run 阻塞在 ctx.Done(),
// Manager 退出(Stop root)时才返回.占根模块("root"节点)让 root 持有"系统整体"
// 这一资源位;子 routine 占 body/leg 等具体模块时声明各自的,互不冲突.
func (m *Manager) initRoot() {
	rootName := m.tree.RootID() // 根模块节点 id(tree.json 里 "root")
	root := command.New("root", func(ctx context.Context, cmd *command.Command) {
		<-ctx.Done() // 长跑,ctx 取消(Manager Stop root)时返回
	})
	// 占根模块(root 是无父 routine,ancestorIDs 空).TryAcquire 正常走 cone 检查.
	_ = m.tree.TryAcquire(root.ID, []string{rootName}, nil)
	root.SetModules(rootName)
	n := &node{cmd: root, parent: nil, declared: root.Modules, clientID: ""}
	m.nodes[root.ID] = n
	m.root = root
	// root 不走 runRemote(不发 lifecycle)----直接 Start 派 goroutine 跑 Run,
	// 然后 MarkRunning 进 running 态(跳过 starting).模块已占好.
	_ = root.Start()
	root.MarkRunning()
}

// RootID 返回 root routine 的 command id.业务 routine / passive 应挂 root 下
// (Execute(m.RootID(), ...) / AutoStartPassive 用 root 作父).
func (m *Manager) RootID() int { return m.root.ID }

// Create 创建一个 routine(created 态):分配 id,记录父,client 归属,但不占模块,
// 不运行.模块核验+占用在 Start 阶段(TryAcquire);created 回报回带的 modules 填
// n.declared,Start 用它 TryAcquire.parentID==0 表示根(不应直接用,走 root).
//
// clientID 按 **name 查 routineClients 路由表** 定----routine 归属由 name 决定
// (定义在哪个 server 的 catalog),不继承父.所以 a@serverA submit b@serverB 时,
// b 的 clientID 指向 serverB(查表),跟父 a 的 clientID 不同----跨 server submit.
// 找不到 name(未注册)返回 error.
//
// routine 体由远端 routine server 提供(按 name 路由),本地不传 Run func.
// kwargs 是 routine 入参(submit kwargs 的单一来源):存入 cmd.Kwargs,runRemote 经
// lifecycle.created 投递给 created().submit 回环路径(OnSubmitCreated)和 Execute
// 直启路径都经此存入.nil = 无入参.
//
// n.declared 初始空----catalog 不再缓存 modules,不在此占位.created 回报由 server
// 在 created() 本地算好回带(static 返固定 list,dynamic 按 kwargs 现算),reader 收到
// 调 OnCreatedModules 填 n.declared----这才是真理源.submitted 回执用
// OnSubmitCreatedModules 取 n.declared 带给父 handle,编排器据此算冲突.
// 所以 Start 前必须 created 回报已到:submit 回环天然满足(OnSubmitCreated 等),
// Execute 直启内部等回报(见 Execute).
func (m *Manager) Create(parentID int, name string,
	kwargs map[string]any) (*command.Command, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	var parent *node
	if parentID != 0 {
		parent = m.nodes[parentID]
		if parent == nil {
			return nil, fmt.Errorf("parent routine %d not found", parentID)
		}
	}
	// 按 name 查路由表定 clientID----不继承父.routine 归属由 name 决定.
	route := m.routineClients[name]
	if route.clientID == "" {
		return nil, fmt.Errorf("routine %q not registered in any server", name)
	}
	cmd := command.New(name, func(ctx context.Context, cmd *command.Command) {
		m.runRemote(ctx, cmd)
	})
	cmd.Kwargs = kwargs
	cmd.ParentCommandID = parentID
	// n.declared 初始空----created 回报(Manager dispatchEvent resolve createdCh)填真值,
	// Start 用它 TryAcquire.future chan 在此建好(cap1),先于任何 send----无注册竞态.
	n := &node{
		cmd:       cmd,
		parent:    parent,
		clientID:  route.clientID,
		createdCh: make(chan conn.CreatedResult, 1),
		startedCh: make(chan struct{}, 1),
		stoppedCh: make(chan map[string]any, 1),
	}
	m.nodes[cmd.ID] = n
	if parent != nil {
		parent.children = append(parent.children, n)
	}
	return cmd, nil
}

// Start 启动一个已 Create 的 routine:模块冲突校验+占用,运行.
// 占用语义:声明什么占什么(不扣祖先覆盖),父子可共占同一模块节点(holders 队列叠加).
// cone 互斥跳过父祖先----外人占同 cone 仍冲突(防抢占),子占父 cone 内节点放行.
// declared 来自 created 回报(OnCreatedModules 填,static=固定 list,dynamic=kwargs
// 现算)----Start 前一定已 created 回报(submit 回环 OnSubmitCreated 等 / Execute
// 直启内部等,都保证 Start 时 declared 已填).
//
// 模块核验在 Start 阶段做:冲突返回 error,reader 发 routine.rejected 回 py.
// 错误带 name#id----TryAcquire 只知 rid 不知 routine name,这里包一层让 rejected
// 回执自包含 routine 标识(Python 不用反查 id).ConflictError 的 holders 是 id
// 列表,翻译成 name#id 让占住者也自包含.Start 失败后 Python 侧 created instance
// 需清理(见 server.on_inbound ROUTINE_REJECTED 分支).
//
// kwargs 是 routine 入参(submit kwargs 来源):存入 cmd.Kwargs,runRemote 经
// lifecycle.start 发给远端 routine.start().nil = 无入参(用 cmd 已存的).
// 通常 start 时不另传 kwargs(沿用 Create 时存的);这里保留参数给 Go 侧直接
// 驱动场景(如 demo)覆盖用.py 侧 handle.start() 不带 kwargs----走 OnStartChild
// 传 nil,沿用 cmd.Kwargs.
func (m *Manager) Start(id int, kwargs map[string]any) error {
	m.mu.Lock()
	n := m.nodes[id]
	if n == nil {
		m.mu.Unlock()
		return fmt.Errorf("routine %d not found", id)
	}
	// kwargs 覆盖进 cmd(runRemote 经 StartRoutine 发给远端).nil 则沿用
	// Create 时存的 cmd.Kwargs(submit 回环 / Execute 直启都已存入).
	if kwargs != nil {
		n.cmd.Kwargs = kwargs
	}
	declared := n.declared
	ancestorIDs := ancestorIDSet(n)
	m.mu.Unlock()

	// 模块占用(冲突校验 + 打 holders tag).失败直接返回,不动 command 状态.
	// 错误自包含:被启动的 routine(name#id)+ 被挡模块 + 占住者(name#id),
	// rejected 回执不用 Python 反查就知道发生什么.
	// 例:output2#32 start failed: module "output" blocked by echo#31
	// 例:module "output" blocked by echo-3
	// 例:module "output" blocked at "figure" by echo-3
	if len(declared) > 0 {
		if err := m.tree.TryAcquire(n.cmd.ID, declared, ancestorIDs); err != nil {
			var ce *module.ConflictError
			if errors.As(err, &ce) {
				holders := make([]string, 0, len(ce.Holders))
				m.mu.Lock()
				for _, h := range ce.Holders {
					if hn, ok := m.nodes[h]; ok {
						holders = append(holders, hn.cmd.String())
					} else {
						holders = append(holders, strconv.Itoa(h))
					}
				}
				m.mu.Unlock()
				blockedBy := strings.Join(holders, ", ")
				if ce.Requested == ce.BlockedBy {
					return fmt.Errorf("module %q blocked by %s", ce.Requested, blockedBy)
				}
				return fmt.Errorf("module %q blocked at %q by %s",
					ce.Requested, ce.BlockedBy, blockedBy)
			}
			return err
		}
	}
	n.cmd.SetModules(declared...)
	return n.cmd.Start()
}

// Execute = Create + 发created等回报 + Start.Start 失败回滚 Create,保持原子.
// clientID 由 Create 内部按 name 查 catalog 路由表定(不继承父,不由调用方指定).
// kwargs 是 routine 入参(submit kwargs 单一来源):created + start 共用.
//
// Create 不发 created(n.declared 空),Execute 这里发 created 等回报填 declared
// 再 Start TryAcquire----跟 submit 回环对称(OnSubmitCreated 等 → OnStartChild Start).
// runRemote 见 createdSent=true 跳过 created 阶段直接发 start.阻塞等 created 回报
// 不死锁:Execute 在 monitorConnect(passive)/main(scenario) goroutine,created 回报
// 经 reader goroutine 投递 chan.等 created 时 runRemote 还没起(Start 未调),
// subs[id] 是 Execute 的 chan;created 成功后 Execute 离开 select 调 Start →
// runRemote 起 → runRemote 再 Stopped(id) 覆盖(Execute 已不等,旧 chan GC).
func (m *Manager) Execute(parentID int, name string,
	kwargs map[string]any) (*command.Command, error) {
	cmd, err := m.Create(parentID, name, kwargs)
	if err != nil {
		return nil, err
	}
	// 发 created 等回报(复用 OnSubmitCreated:发 lifecycle.created + 注册 created
	// waiter + 设 createdSent=true).跨 server 正确路由(用子 routine 的 client).
	createdCh, err := m.OnSubmitCreated(cmd.ID, name, kwargs, parentID)
	if err != nil {
		m.removeNode(cmd.ID)
		return nil, err
	}
	// 等 created 回报(created 失败也经 createdCh 回流:resolveStopped 在 !createdDone
	// 时 stopped resolve createdCh with err;conn.down 的 failPending 同样 resolve createdCh).
	m.mu.Lock()
	n := m.nodes[cmd.ID]
	clientID := ""
	if n != nil {
		clientID = n.clientID
	}
	m.mu.Unlock()
	var createdErr error
	if m.Conn(clientID) != nil {
		select {
		case r := <-createdCh:
			// created 回报带 modules(created() 返回值)----存进 n.declared,Start 的
			// TryAcquire 用.created 失败(r.Err != nil)则不填,直接返回 err.
			createdErr = r.Err
			if r.Err == nil {
				m.mu.Lock()
				n.declared = r.Modules
				m.mu.Unlock()
			}
		}
	} else {
		createdErr = fmt.Errorf("routine %s: conn not found", cmd)
	}
	if createdErr != nil {
		m.removeNode(cmd.ID)
		return nil, createdErr
	}
	if err := m.Start(cmd.ID, nil); err != nil {
		m.removeNode(cmd.ID)
		return nil, err
	}
	return cmd, nil
}

// Stop 停止 routine:先级联停所有子,再停自己("父必须等子").
// 显式 stop(Go API / Execute / scenario demo 调).日志 "stop X#N" 在此打.
func (m *Manager) Stop(id int) {
	m.mu.Lock()
	n, ok := m.nodes[id]
	if !ok || n.stopping {
		m.mu.Unlock()
		return
	}
	n.stopping = true
	m.mu.Unlock()
	m.log.Infof("🛑 stop %s", n.cmd)
	m.stop(n, false, 0)
}

// stop 停 routine n:先递归级联停子,再停自己.
//
// cascade=true:由父级联触发.子可能 running 或 created----我们停它,打 "stop (cascade)".
// cascade=false:顶层----caller(OnStopChild/Stop/OnRoutineTerminated)已设 stopping 标志
// 并打了 "stop"/"stopped",本函数顶层不打日志.
//
// 幂等:cascade 子查 stopping 标志,已设则跳过(子的 OnRoutineTerminated 或另一级联已接管).
//
// created 子(无 body,永不自停):cleanupFailedStart + 发 stopped 让 Python 清 instance.
// running/starting:cmd.Stop 等 body 退出(已退出则 no-op).
func (m *Manager) stop(n *node, cascade bool, forceBy int) {
	if cascade {
		m.mu.Lock()
		if n.stopping {
			m.mu.Unlock()
			return // 子已被自己的 OnRoutineTerminated / 显式 stop 接管
		}
		n.stopping = true
		if forceBy > 0 {
			// force 驱逐:标记本节点 stop 原因为 force(runRemote 发 lifecycle.stop 时
			// 带上 reason=force + by=forceBy,让被驱逐者 on_done 走紧急退让分支).
			// 递归子也带同一 forceBy----整棵被驱逐子树都按 force 停.
			n.stopReason = "force"
			n.stopBy = forceBy
		}
		m.mu.Unlock()
	}

	// 快照子(子退出会反向修改 n.children)
	m.mu.Lock()
	children := make([]*node, len(n.children))
	copy(children, n.children)
	m.mu.Unlock()

	// 先递归停子("父必须等子")
	for _, c := range children {
		m.stop(c, true, forceBy)
	}

	if n.cmd.State() == command.StateCreated {
		// created 态(已 submit 未 start):没 body,没 runner,发 lifecycle.destroy 让
		// py 销毁 instance(handle_destroy 直接 _cleanup + 回 stopped,跳过 stop hook----created 无 body).
		// Go 侧先清自己(cleanupFailedStart:清 pubsub 订阅 + removeNode)----node 删掉后
		// py 回的 stopped 触发 OnRoutineTerminated 但 node 不在,no-op(幂等).
		// 不调 cmd.Stop(created 态 no-op);removeNode 已含 delete,不重复.
		// created 子无 body 可等----销毁即完成,打 🗑️ ... destroyed(状态,routine 在前).
		// 用 destroyed(对齐 wire event lifecycle.destroy)----created 态没 start 过,
		// 不能叫 stopped(停止一个从没启动的东西语义不通).forceBy 对 created 无意义
		// (destroy 不带 reason),子树里 created 的就直接销毁.
		if cascade {
			m.log.Infof("🗑️ %s destroyed (created, cascade)", n.cmd)
		}
		m.cleanupFailedStart(n.cmd.ID)
		// 发 lifecycle.destroy 让 py 销毁 created instance(经 bus 出站,跨 server 正确路由).
		m.sendDestroy(n.clientID, strconv.Itoa(n.cmd.ID))
		return
	}

	// running/starting:cascade 才打日志(顶层由 caller 打).force 标 reason.
	if cascade {
		if forceBy > 0 {
			m.log.Infof("🛑 stop %s (force, cascade)", n.cmd)
		} else {
			m.log.Infof("🛑 stop %s (cascade)", n.cmd)
		}
	}
	n.cmd.Stop() // 自然终止时 body 已退出,no-op;显式 stop / 级联则等 body 退出
	m.mu.Lock()
	delete(m.nodes, n.cmd.ID)
	if n.parent != nil {
		removeChild(n.parent, n)
	}
	m.mu.Unlock()
}

func (m *Manager) removeNode(id int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	n := m.nodes[id]
	if n == nil {
		return
	}
	delete(m.nodes, id)
	if n.parent != nil {
		removeChild(n.parent, n)
	}
}

// OnRoutineTerminated 在 routine 真正终止(reader 收到 lifecycle.stopped)时调:
// 异步级联 stop 子(子可能还在 running / created)+ 清自己 node.自然结束时
// runRemote 只 Release 模块,不级联清子 / 不清 node----由 reader 在此触发统一清理.
// 被显式 stop 时 stop(n) 已清 node,此处 node 不在,no-op(幂等).
//
// 异步 go m.stop(n):reader 不阻塞----stop running 子会等其 body 退出,要 reader
// 继续投递子 stopped 才能推进.running 态 n 已 stopped,cmd.Stop no-op 立即返回;
// created 态 n 不会自己发 stopped(reader 不触发),靠父 stop(n) 级联清.
func (m *Manager) OnRoutineTerminated(id int) {
	m.mu.Lock()
	n, ok := m.nodes[id]
	if !ok || n.stopping {
		// node 已清(显式 stop 的 stop(n) 已 delete)或显式 stop 接管中(OnStopChild
		// 已设 stopping 并打了 "stop")----都不该再打 "stopped".
		m.mu.Unlock()
		return
	}
	n.stopping = true
	m.mu.Unlock()
	// 自然终止:reader 收到 lifecycle.stopped(routine 自己 return / error).
	// 显式 stop 时 stop(n) 会先 delete node,此处 ok=false 跳过;若竞态 node 还在,
	// stopping=true(OnStopChild 同步设)也跳过.所以到这里一定是自然终止.
	// 区别于 🛑 stop(被显式 stop / 级联).
	m.log.Infof("✅ %s stopped", n.cmd)
	go m.stop(n, false, 0)
}

// ancestorIDSet 返回 n 的 routine 树祖先 command id 集合(不含 n 自己).
// 给 TryAcquire 判 cone 共占:被占节点的 holder 若全是自己或祖先,放行.
func ancestorIDSet(n *node) map[int]struct{} {
	out := map[int]struct{}{}
	for p := n.parent; p != nil; p = p.parent {
		out[p.cmd.ID] = struct{}{}
	}
	return out
}

func removeChild(parent, child *node) {
	for i, c := range parent.children {
		if c == child {
			parent.children = append(parent.children[:i], parent.children[i+1:]...)
			return
		}
	}
}
