package conn

// 事件协议:v3 平铺事件(msg["event"] 分发).
// Req unary 走查询;Stream 双向流走 lifecycle 控制/回报.
// 传输无关----gRPC / 将来的 jsonrpc / ws 都用这套词汇.

// lifecycle 事件(走 Stream 双向流).
const (
	LifecycleStart   = "lifecycle.start"   // 调度器 → server:启动 routine
	LifecycleStop    = "lifecycle.stop"    // 调度器 → server:打断 routine(started 态)
	LifecycleDestroy = "lifecycle.destroy" // 调度器 → server:销毁 created 态 routine(未 start,无 body)
	LifecycleStarted = "lifecycle.started" // server → 调度器:已启动
	LifecycleStopped = "lifecycle.stopped" // server → 调度器:已停止(带 reason)
	LifecycleCreated = "lifecycle.created" // 双向:调度器→server 实例化+注册;server→调度器 created 回报(kernel 中转回 py 唤醒 wait_created)
)

// Req 查询事件.
const (
	ReqGetModules           = "get_modules"
	ReqGetRoutines          = "get_routines"
	ReqGetRouters           = "get_routers"
	ReqGetRoutineFromRouter = "get_routine_from_router"
	ReqGetModuleTree        = "get_module_tree" // dial-in routine→kernel 拉 module.tree
	ReqGetRunningRoutines   = "get_running_routines"  // dial-in routine→kernel 查 running 实例 [{name,id}]
)

// stopped 回报里的 reason 取值.
const (
	ReasonStop    = "STOP"
	ReasonError   = "ERROR"
	ReasonUnknown = "UNKNOWN"
)

// routine→kernel catalog 推送(dial-in 专用,走 Stream).
// dial-out 下 kernel 用 Req(get_modules/get_routines) 拉 catalog(同步往返);
// dial-in 方向矛盾(kernel 不能调 routine Req),改由 routine 连上后主动 push 一次
// catalog(routines + modules),kernel 收到注册路由表 + 回推 module.tree.
const CatalogPush = "catalog.push"

// catalog 增量变更(routine 运行时 register/reload/deregister 单条同步,走 Stream).
// 三层语义:register 加一条(同名 fail),reload 覆盖一条(不区分 conn),deregister 删一条.
// 配合 catalog.push 全量(重连首帧)用----运行期变更走单条,即时精确,不用重推全量.
//
// 带 req_id + 回执(catalog.registered / catalog.reloaded / catalog.deregistered):
// kernel 是唯一真理源,py 等 kernel 回执 ok=true 才本地 Routines 操作;ok=false
// (register 同名冲突 / deregister name 不存在)抛错,本地不动.reload 总 ok=true
// (覆盖语义,不冲突).对称 reg/reload/dereg 流程.
//
// deregister 走两跳(knowledge of holder):kernel 不直接删路由,而是先发
// catalog.deregister.cmd 给持有者 hub → hub 本地 dereg → 回执 catalog.deregister.cmd.ack
// → kernel 删路由 + 回执请求者 catalog.deregistered.支持跨 hub dereg(请求者 ≠ 持有者).
// req_id 贯穿整个流程(请求者→kernel→持有者→kernel→请求者).
const (
	CatalogRegister          = "catalog.register"           // py→kernel: 注册单条 routine(同名 fail,带 req_id+name/is_passive/meta)
	CatalogReload            = "catalog.reload"             // py→kernel: 重载单条 routine(不区分 conn 覆盖,带 req_id+name/is_passive/meta)
	CatalogDeregister        = "catalog.deregister"         // py→kernel: 请求移除单条 routine(带 req_id+name)
	CatalogDeregisterCmd     = "catalog.deregister.cmd"     // kernel→py: 通知持有者 hub 本地 dereg(带 req_id+name)
	CatalogDeregisterCmdAck  = "catalog.deregister.cmd.ack" // py→kernel: 持有者 hub 本地 dereg 后回执(带 req_id+ok+error?)
	CatalogRegistered        = "catalog.registered"         // kernel→py: register 回执(带 req_id+ok+error?)
	CatalogReloaded          = "catalog.reloaded"           // kernel→py: reload 回执(带 req_id+ok+error?)
	CatalogDeregistered      = "catalog.deregistered"       // kernel→py: deregister 回执(带 req_id+ok+error?)
	CatalogPushed            = "catalog.pushed"             // kernel→py: 全量 push 回执(带 req_id+registered[]+skipped[])
)

// routine 调 routine 反向事件(py→kernel,走同一条 Stream 双向通道).
// submit 经 kernel 回环:kernel 仍是唯一调度权威(冲突检测+模块占用+父子关系).
const (
	RoutineSubmit       = "routine.submit"        // py→kernel: 建子命令(带 req_id 回执)
	RoutineUnsubmit     = "routine.unsubmit"      // py→kernel: 撤销提交(清 created 态子命令,未 start)
	RoutineStart        = "routine.start"         // py→kernel: start 子命令
	RoutineStop         = "routine.stop"          // py→kernel: stop 子命令
	RoutineSubmitted    = "routine.submitted"     // kernel→py: submit 回执(带 req_id + child_id)
	RoutineRejected     = "routine.rejected"      // kernel→py: start/stop 被拒(父未 started 时不能 start/stop 子;带 op + child_id + error)
	RoutineAcquire      = "routine.acquire"       // py→kernel: 运行时占领模块(带 req_id + id + modules)
	RoutineAcquired     = "routine.acquired"      // kernel→py: acquire 回执(带 req_id + ok + error)
	RoutineRelease      = "routine.release"       // py→kernel: 运行时释放模块(带 req_id + id + modules)
	RoutineReleased     = "routine.released"      // kernel→py: release 回执(带 req_id + ok)
	RoutineForceRelease = "routine.force_release" // py→kernel: 强制释放模块(驱逐 cone 内第三方 holder 后空出)
	RoutineForceAcquire = "routine.force_acquire" // py->kernel: 强制占领模块(驱逐 cone 内第三方 holder 后自己占住,带驱逐的 acquire)
	RoutineForceStart   = "routine.force_start"   // py→kernel: 抢占式 start 子(驱逐占住子 declared 模块的第三方后 start)
	RoutineGetRunning   = "routine.get_running"   // py(dial-out)->kernel: 查 running 实例(带 req_id,kernel 回 get_running_reply)
	RoutineGetRunningReply = "routine.get_running_reply" // kernel->py: get_running 回执(带 req_id + routines [{name,id}])
	RoutineGetModuleTree      = "routine.get_module_tree"       // py(dial-out)->kernel: 拉 module.tree(带 req_id,kernel 回 get_module_tree_reply)
	RoutineGetModuleTreeReply = "routine.get_module_tree_reply" // kernel->py: get_module_tree 回执(带 req_id + tree)
	RoutineLoadModule     = "routine.load_module"     // py->kernel: 往父模块加载子模块(带 req_id+parent_id+child_id,kernel 回 module_loaded)
	RoutineModuleLoaded   = "routine.module_loaded"  // kernel->py: load_module 回执(带 req_id + ok + error)
	RoutineUnloadModule   = "routine.unload_module"   // py->kernel: 卸载子模块(带 req_id+child_id,kernel 回 module_unloaded)
	RoutineModuleUnloaded = "routine.module_unloaded" // kernel->py: unload_module 回执(带 req_id + ok + error)
	RoutineRenameModule   = "routine.rename_module"   // py->kernel: 重命名模块(带 req_id+id+new_name,kernel 回 module_renamed)
	RoutineModuleRenamed  = "routine.module_renamed"  // kernel->py: rename_module 回执(带 req_id + ok + error)
	RoutineMoveModule     = "routine.move_module"     // py->kernel: 移动模块到新父下(带 req_id+id+new_parent_id,kernel 回 module_moved)
	RoutineModuleMoved    = "routine.module_moved"    // kernel->py: move_module 回执(带 req_id + ok + error)
)

// routine↔routine 通信事件(py→kernel→py,kernel 做 broker 转发).
// 全部走 message.* 前缀(见下)----各子类型独立 wire,字段自洽,不靠 topic 耦合.
// kernel 对 message.* 全是 dumb forward----不解析 envelope(__req_id__/__stream_id__
// 是纯 python envelope,python 侧管).

// message.* 事件(py→kernel→py,kernel dumb forward by target_id).
// 语义分组:消息类全归 message 前缀,各子类型独立 wire,
// 字段自洽,不靠 topic 耦合.kernel 对 message.* 全是 dumb forward----不解析
// envelope(__req_id__/__stream_id__ 等都在 envelope 里,python 侧管).
//
//   message.send         → message.delivered          纯定向发,调 on_message(source,data)
//   message.req          → message.req_delivered       req 到达 provider(按 envelope event 路由 @request)
//   message.req_reply    → message.req_reply_delivered 回执到达 caller(resolve req future)
//   message.stream_open  → message.stream_open_delivered  开流到达 provider(@stream)
//   message.stream_data  → message.stream_data_delivered  数据/eof 到达 caller(喂 StreamReader)
//   message.stream_cancel → message.stream_cancel_delivered  取消到达 provider
//
// 各子类型的 delivered 配对一一对应,wire 上能看出语义;接收端再按 envelope 内容
// 精确 demux.message.delivered 走 on_message(可能并发 fire,业务自带 id reorder).
const (
	MessageSend              = "message.send"
	MessageDelivered         = "message.delivered"
	MessageReq               = "message.req"
	MessageReqDelivered      = "message.req_delivered"
	MessageReqReply          = "message.req_reply"
	MessageReqReplyDelivered = "message.req_reply_delivered"
	MessageStreamOpen        = "message.stream_open"
	MessageStreamOpenDelivered = "message.stream_open_delivered"
	MessageStreamData        = "message.stream_data"
	MessageStreamDataDelivered = "message.stream_data_delivered"
	MessageStreamCancel      = "message.stream_cancel"
	MessageStreamCancelDelivered = "message.stream_cancel_delivered"
)

// pubsub 事件(py→kernel→py,kernel 维护订阅表做 fanout).
const (
	PubsubSubscribe   = "pubsub.subscribe"   // py→kernel: 订阅 topic
	PubsubUnsubscribe = "pubsub.unsubscribe" // py→kernel: 退订 topic
	PubsubPublish     = "pubsub.publish"     // py→kernel: 发 topic
	PubsubDelivered   = "pubsub.delivered"   // kernel→py: 投递给订阅者
)

// yield 事件(child→parent routine yield,kernel dumb forward).
const (
	RoutineYield          = "routine.yield"   // child→kernel: yield 一项(is_final=true 收尾)
	RoutineYielded        = "routine.yielded" // kernel→parent: 转发(按 id=child_id 路由到 handle)
)

// Event 取消息的 event 字段;缺失返回空串.
func Event(msg map[string]any) string {
	if v, ok := msg["event"].(string); ok {
		return v
	}
	return ""
}

// ModuleTree:kernel→server 推模块树拓扑(静态 config,运行期不变).
// server 缓存后本地算 cone/conflict----业务侧编排策略(如 AutoSP 自动串并行)用.
// 跟 catalog 拉取同一窗口(stream ready 后)推一次;reconnect 重推.fire-and-forget.
const ModuleTree = "module.tree"
