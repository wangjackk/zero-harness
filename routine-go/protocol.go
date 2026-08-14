package routine

// Wire 协议常量.对标 Python routine/protocol.py + kernel/conn/events.go.
// event 字段平铺分发,Req unary 走查询,Stream 双向流走 lifecycle 控制/回报.

// lifecycle 事件(走 Stream 双向流).
const (
	LifecycleCreated = "lifecycle.created" // 双向:调度器→server 实例化;server→调度器 created 回报
	LifecycleStart   = "lifecycle.start"   // 调度器 → server:启动 routine
	LifecycleStop    = "lifecycle.stop"    // 调度器 → server:打断 routine(started 态)
	LifecycleDestroy = "lifecycle.destroy" // 调度器 → server:销毁 created 态 routine(未 start)
	LifecycleStarted = "lifecycle.started" // server → 调度器:已启动
	LifecycleStopped = "lifecycle.stopped" // server → 调度器:已停止(带 reason)
)

// stopped 回报 reason 取值(对标 Python ControlDoneReason).
const (
	ReasonUnknown    = "UNKNOWN"
	ReasonAuto       = "AUTO"
	ReasonStop       = "STOP"
	ReasonError      = "ERROR"
	ReasonCancel     = "CANCEL"
	ReasonForce      = "FORCE"
	ReasonDisconnect = "DISCONNECT"
)

// reason 语义字符串 → wire enum 映射.
var reasonToEnum = map[string]string{
	"auto":       ReasonAuto,
	"stop":       ReasonStop,
	"error":      ReasonError,
	"cancel":     ReasonCancel,
	"force":      ReasonForce,
	"disconnect": ReasonDisconnect,
}

// Req 查询事件(走 Req unary).
const (
	ReqGetModules         = "get_modules"
	ReqGetRoutines        = "get_routines"
	ReqGetModuleTree      = "get_module_tree"
	ReqGetRunningRoutines = "get_running_routines"
)

// routine 调 routine 反向事件(py→kernel,走同一条 Stream).
const (
	RoutineSubmit            = "routine.submit"
	RoutineUnsubmit          = "routine.unsubmit"
	RoutineStart             = "routine.start"
	RoutineStop              = "routine.stop"
	RoutineSubmitted         = "routine.submitted"
	RoutineRejected          = "routine.rejected"
	RoutineAcquire           = "routine.acquire"
	RoutineAcquired          = "routine.acquired"
	RoutineRelease           = "routine.release"
	RoutineReleased          = "routine.released"
	RoutineForceRelease      = "routine.force_release"
	RoutineForceAcquire      = "routine.force_acquire"
	RoutineForceStart        = "routine.force_start"
	RoutineGetRunning        = "routine.get_running"
	RoutineGetRunningReply   = "routine.get_running_reply"
	RoutineGetModuleTree     = "routine.get_module_tree"
	RoutineGetModuleTreeReply = "routine.get_module_tree_reply"
	RoutineLoadModule        = "routine.load_module"
	RoutineModuleLoaded      = "routine.module_loaded"
	RoutineUnloadModule      = "routine.unload_module"
	RoutineModuleUnloaded    = "routine.module_unloaded"
)

// message.* 事件(py→kernel→py,kernel dumb forward by target_id).
const (
	MessageSend                = "message.send"
	MessageDelivered           = "message.delivered"
	MessageReq                 = "message.req"
	MessageReqDelivered        = "message.req_delivered"
	MessageReqReply            = "message.req_reply"
	MessageReqReplyDelivered   = "message.req_reply_delivered"
	MessageStreamOpen          = "message.stream_open"
	MessageStreamOpenDelivered = "message.stream_open_delivered"
	MessageStreamData          = "message.stream_data"
	MessageStreamDataDelivered = "message.stream_data_delivered"
	MessageStreamCancel        = "message.stream_cancel"
	MessageStreamCancelDelivered = "message.stream_cancel_delivered"
)

// pubsub 事件(py→kernel→py,kernel 维护订阅表 fanout).
const (
	PubsubSubscribe   = "pubsub.subscribe"
	PubsubUnsubscribe = "pubsub.unsubscribe"
	PubsubPublish     = "pubsub.publish"
	PubsubDelivered   = "pubsub.delivered"
)

// yield 事件(child→parent routine yield,kernel dumb forward).
const (
	RoutineYield          = "routine.yield"
	RoutineYielded        = "routine.yielded"
)

// module.tree:kernel→server 推模块树拓扑(静态 config,运行期不变).
const ModuleTreeEvent = "module.tree"

// catalog 事件(dial-in push + 增量 register/reload/deregister).
const (
	CatalogPush             = "catalog.push"
	CatalogRegister         = "catalog.register"
	CatalogReload           = "catalog.reload"
	CatalogDeregister       = "catalog.deregister"
	CatalogDeregisterCmd    = "catalog.deregister.cmd"
	CatalogDeregisterCmdAck = "catalog.deregister.cmd.ack"
	CatalogRegistered       = "catalog.registered"
	CatalogReloaded         = "catalog.reloaded"
	CatalogDeregistered     = "catalog.deregistered"
	CatalogPushed           = "catalog.pushed"
)

// envelope 保留字段名(放进 message.data 里,对 kernel 透明).
const (
	EnvelopeReqID    = "__req_id__"
	EnvelopeReplyTo  = "__reply_to__"
	EnvelopeStreamID = "__stream_id__"
	EnvelopeEvent    = "event"
	EnvelopeData     = "data"
	EnvelopeOK       = "ok"
	EnvelopeError    = "error"
	EnvelopeChunk    = "chunk"
	EnvelopeEOF      = "__eof__" // 值:done / error / cancelled
	EnvelopeCancel   = "__cancel__"
)

// Event 取消息的 event 字段;缺失返回空串.
func Event(msg map[string]any) string {
	if v, ok := msg["event"].(string); ok {
		return v
	}
	return ""
}
