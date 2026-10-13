/** routine wire 协议常量(与 py zero/routine-py/routine/protocol.py 同名同义,零序列化 in-proc).

 in-proc 传输下消息就是对象本体;常量层与 py 版 wire 完全对齐,
 将来换 transport 时事件名不变.
 */
/** wire 消息(envelope/事件帧,transport 与 dispatch 层的类型). */
export type Msg = Record<string, any>

/** 业务载荷(routine kwargs / req data----嵌入 envelope 的用户数据). */
export type Payload = Record<string, any>

export const ControlDoneReason = {
  UNKNOWN: 'UNKNOWN',
  AUTO: 'AUTO',
  STOP: 'STOP',
  DESTROYED: 'DESTROYED',
  ERROR: 'ERROR',
  CANCEL: 'CANCEL',
  FORCE: 'FORCE',
  DISCONNECT: 'DISCONNECT',
} as const
export type ControlDoneReason = (typeof ControlDoneReason)[keyof typeof ControlDoneReason]

// --- lifecycle(kernel ↔ hub)---
export const LIFECYCLE_CREATED = 'lifecycle.created'      // kernel→hub 实例化;hub→kernel created 回报(带 modules)
export const LIFECYCLE_START = 'lifecycle.start'          // kernel→hub 启动 routine
export const LIFECYCLE_STOP = 'lifecycle.stop'            // kernel→hub 打断 routine(带 reason=force/by 时为驱逐)
export const LIFECYCLE_DESTROY = 'lifecycle.destroy'      // kernel→hub 销毁 created 态 routine
export const LIFECYCLE_STARTED = 'lifecycle.started'      // hub→kernel 已启动
export const LIFECYCLE_STOPPED = 'lifecycle.stopped'      // hub→kernel 已停止(带 reason/result/error)

// --- routine 调 kernel 请求/回执(hub→kernel→hub)---
export const ROUTINE_CREATE = 'routine.create'
export const ROUTINE_CREATED = 'routine.created'
export const ROUTINE_REJECTED = 'routine.rejected'
export const ROUTINE_START = 'routine.start'   // 带可选 priority(number, 缺省 10=常规默认档; 0~9 低档): 冲突时 incoming > holder 才驱逐后 start
export const ROUTINE_STOP = 'routine.stop'
export const ROUTINE_DESTROY = 'routine.destroy'
export const ROUTINE_ACQUIRE = 'routine.acquire'
export const ROUTINE_ACQUIRED = 'routine.acquired'
export const ROUTINE_RELEASE = 'routine.release'
export const ROUTINE_RELEASED = 'routine.released'
export const ROUTINE_LOAD_MODULE = 'routine.load_module'
export const ROUTINE_MODULE_LOADED = 'routine.module_loaded'
export const ROUTINE_UNLOAD_MODULE = 'routine.unload_module'
export const ROUTINE_MODULE_UNLOADED = 'routine.module_unloaded'

// --- message.*(kernel dumb forward by target_id)---
export const MESSAGE_SEND = 'message.send'
export const MESSAGE_DELIVERED = 'message.delivered'
export const MESSAGE_REQ = 'message.req'
export const MESSAGE_REQ_DELIVERED = 'message.req_delivered'
export const MESSAGE_REQ_REPLY = 'message.req_reply'
export const MESSAGE_REQ_REPLY_DELIVERED = 'message.req_reply_delivered'
export const MESSAGE_STREAM_OPEN = 'message.stream_open'
export const MESSAGE_STREAM_OPEN_DELIVERED = 'message.stream_open_delivered'
export const MESSAGE_STREAM_DATA = 'message.stream_data'
export const MESSAGE_STREAM_DATA_DELIVERED = 'message.stream_data_delivered'
export const MESSAGE_STREAM_CANCEL = 'message.stream_cancel'
export const MESSAGE_STREAM_CANCEL_DELIVERED = 'message.stream_cancel_delivered'

// --- pubsub(kernel 维护订阅表 fanout)---
export const PUBSUB_SUBSCRIBE = 'pubsub.subscribe'
export const PUBSUB_UNSUBSCRIBE = 'pubsub.unsubscribe'
export const PUBSUB_PUBLISH = 'pubsub.publish'
export const PUBSUB_DELIVERED = 'pubsub.delivered'

// --- lifecycle 观察流(conn 级观察者,kernel 内 routine 树状态变更的原始广播)---
// 与 pubsub 的分野:pubsub 是 topic 寻址的 routine 间通信;lifecycle 是框架
// 内部状态变更的原始流,kernel 不感知消费者语义(前端树渲染/CLI 调试/审计
// 都订阅同一流).lifecycle 事件照常单播 relay 给父 handle,观察流是旁路副本.
export const LIFECYCLE_WATCH = 'lifecycle.watch'    // conn→kernel: 开启观察(幂等,带 req_id)
export const LIFECYCLE_UNWATCH = 'lifecycle.unwatch'  // conn→kernel: 关闭观察
export const LIFECYCLE_SNAPSHOT = 'lifecycle.snapshot' // kernel→conn: enable 回执(当前树快照,带 req_id+seq)
export const LIFECYCLE_EVENT = 'lifecycle.event'                  // kernel→conn: 事件帧(type=created/started/stopped/destroyed)

// --- yield(child→kernel→parent,kernel dumb forward)---
export const ROUTINE_YIELD = 'routine.yield'
export const ROUTINE_YIELDED = 'routine.yielded'

// --- module tree(kernel→hub 推送拓扑)---
export const MODULE_TREE = 'module.tree'

// --- catalog(hub→kernel 注册 / kernel 回执)---
export const CATALOG_PUSH = 'catalog.push'
export const CATALOG_PUSHED = 'catalog.pushed'
export const CATALOG_REGISTER = 'catalog.register'
export const CATALOG_REGISTERED = 'catalog.registered'
export const CATALOG_RELOAD = 'catalog.reload'
export const CATALOG_RELOADED = 'catalog.reloaded'
export const CATALOG_DEREGISTER = 'catalog.deregister'
export const CATALOG_DEREGISTERED = 'catalog.deregistered'
export const CATALOG_DEREGISTER_CMD = 'catalog.deregister.cmd'
export const CATALOG_DEREGISTER_CMD_ACK = 'catalog.deregister.cmd.ack'

// --- Req 查询事件(hub→kernel 同步直达)---
export const REQ_GET_RUNNING = 'get_running_routines'
export const REQ_GET_MODULE_TREE = 'get_module_tree'
export const REQ_GET_ROUTINES = 'get_routines'

// --- envelope 保留字段(message.* 的 data 内,kernel 透明)---
export const ENVELOPE_REQ_ID = '__req_id__'
export const ENVELOPE_REPLY_TO = '__reply_to__'
export const ENVELOPE_STREAM_ID = '__stream_id__'
export const ENVELOPE_EVENT = 'event'
export const ENVELOPE_DATA = 'data'
export const ENVELOPE_OK = 'ok'
export const ENVELOPE_ERROR = 'error'
export const ENVELOPE_CHUNK = 'chunk'
export const ENVELOPE_EOF = '__eof__'      // 值: done / error / cancelled
export const ENVELOPE_CANCEL = '__cancel__'
