"""精简 routine SDK 的 wire 协议常量.

平铺事件名(msg["event"] 分发),只保留
create / start / stop 三类生命周期事件 + Req 查询事件.

wire 格式: Frame{payload=JSON string} ---- 整条平铺消息一次编码/解码,
data 等业务字段随 payload 整体编解码, 无嵌套转义.
"""
from __future__ import annotations

from enum import Enum

import orjson
from .grpc.routine_pb2 import Frame


def dict_to_frame(d: dict) -> Frame:
    """dict -> Frame(payload=紧凑 JSON 文本).

    orjson 默认即紧凑分隔符 + 非 ASCII 直出(等价 stdlib 的
    ensure_ascii=False, separators=(',',':')), 输出 bytes 再 decode 成
    proto 要求的 string.
    """
    return Frame(payload=orjson.dumps(d).decode())


def frame_to_dict(f: Frame) -> dict:
    """Frame -> dict.格式不对直接抛 JSONDecodeError 暴露协议破坏,不做兼容读."""
    return orjson.loads(f.payload)


class ControlDoneReason(str, Enum):
    """lifecycle.stopped 回报里的 reason 取值.

    与 ``Routine.on_stopped`` 的 reason 字符串一一对应(enum 大写上 wire,on_stopped
    小写给业务):auto->AUTO / stop->STOP / error->ERROR / cancel->CANCEL /
    force->FORCE / disconnect->DISCONNECT.UNKNOWN 保留作默认/未知兜底.kernel 对
    reason 是 dumb-forward(不按值分流),故扩值无需 kernel 侧配合.
    """

    UNKNOWN = "UNKNOWN"
    AUTO = "AUTO"
    STOP = "STOP"
    ERROR = "ERROR"
    CANCEL = "CANCEL"
    FORCE = "FORCE"
    DISCONNECT = "DISCONNECT"


# lifecycle 事件(走 Stream 双向流).
LIFECYCLE_CREATED = "lifecycle.created"      # 双向:调度器→server 实例化+注册;server→调度器 created 回报
LIFECYCLE_START = "lifecycle.start"      # 调度器 → server:启动 routine
LIFECYCLE_STOP = "lifecycle.stop"        # 调度器 → server:打断 routine(started 态)
LIFECYCLE_DESTROY = "lifecycle.destroy"  # 调度器 → server:销毁 created 态 routine(未 start,无 yield)
LIFECYCLE_STARTED = "lifecycle.started"  # server → 调度器:已启动
LIFECYCLE_STOPPED = "lifecycle.stopped"  # server → 调度器:已停止(带 reason)

# Req 查询事件(走 Req unary).
REQ_EVENT_GET_MODULES = "get_modules"
REQ_EVENT_GET_ROUTINES = "get_routines"
REQ_EVENT_GET_MODULE_TREE = "get_module_tree"  # dial-in routine->kernel 拉 module.tree
REQ_EVENT_GET_RUNNING_ROUTINES = "get_running_routines"  # dial-in routine→kernel 查 running 实例 [{name,id}]

# routine↔routine 通信事件(py→kernel→py,kernel 做 broker 转发).
# message.* 是 dumb forwarder:kernel 按 target_ids 逐个转发成对应的 delivered.
# 语义分组:消息类全归 message 前缀,各子类型独立 wire,
# 字段自洽,不靠 topic 耦合.envelope(__req_id__/__stream_id__/event 等)全在
# data 里,kernel 不解析,python 侧 demux.
#
#   message.send         → message.delivered          纯定向发,调 on_message(source,data)
#   message.req          → message.req_delivered       req 到达 provider(按 envelope event 路由 @request)
#   message.req_reply    → message.req_reply_delivered 回执到达 caller(resolve req future)
#   message.stream_open  → message.stream_open_delivered  开流到达 provider(@stream)
#   message.stream_data  → message.stream_data_delivered  数据/eof 到达 caller(喂 StreamReader)
#   message.stream_cancel → message.stream_cancel_delivered  取消到达 provider
MESSAGE_SEND = "message.send"
MESSAGE_DELIVERED = "message.delivered"
MESSAGE_REQ = "message.req"
MESSAGE_REQ_DELIVERED = "message.req_delivered"
MESSAGE_REQ_REPLY = "message.req_reply"
MESSAGE_REQ_REPLY_DELIVERED = "message.req_reply_delivered"
MESSAGE_STREAM_OPEN = "message.stream_open"
MESSAGE_STREAM_OPEN_DELIVERED = "message.stream_open_delivered"
MESSAGE_STREAM_DATA = "message.stream_data"
MESSAGE_STREAM_DATA_DELIVERED = "message.stream_data_delivered"
MESSAGE_STREAM_CANCEL = "message.stream_cancel"
MESSAGE_STREAM_CANCEL_DELIVERED = "message.stream_cancel_delivered"


# pubsub 事件(py→kernel→py,kernel 维护订阅表做 fanout).
PUBSUB_SUBSCRIBE = "pubsub.subscribe"     # py→kernel: 订阅 topic
PUBSUB_UNSUBSCRIBE = "pubsub.unsubscribe"  # py→kernel: 退订 topic
PUBSUB_PUBLISH = "pubsub.publish"        # py→kernel: 发 topic
PUBSUB_DELIVERED = "pubsub.delivered"     # kernel→py: 投递给订阅者

# yield 事件(child→parent routine yield,kernel dumb forward).
ROUTINE_YIELD = "routine.yield"              # child→kernel: yield 一项(is_final=true 收尾)
ROUTINE_YIELDED = "routine.yielded"          # kernel→parent: 转发(按 id=child_id 路由到 handle)

# 运行时模块占领/释放(py→kernel,走同一条 Stream).
# 跟类静态声明同一底层 TryAcquire/Release,只是触发在 run() 体里(用户主动调).
# acquire 冲突要拿结果,必须等 acquired ack;release 等 released ack 保持对称.
ROUTINE_ACQUIRE = "routine.acquire"    # py→kernel: 运行时占领模块(带 req_id + id + modules)
ROUTINE_ACQUIRED = "routine.acquired"  # kernel→py: acquire 回执(带 req_id + ok + error)
ROUTINE_RELEASE = "routine.release"    # py→kernel: 运行时释放模块(带 req_id + id + modules)
ROUTINE_RELEASED = "routine.released"  # kernel→py: release 回执(带 req_id + ok)
ROUTINE_FORCE_RELEASE = "routine.force_release"  # py->kernel: 强制释放模块(驱逐 cone 内第三方 holder 后空出,不自己占)
ROUTINE_FORCE_ACQUIRE = "routine.force_acquire"  # py->kernel: 强制占领模块(驱逐 cone 内第三方 holder 后自己占住,带驱逐的 acquire;ack 复用 routine.acquired)
ROUTINE_FORCE_START = "routine.force_start"      # py->kernel: 抢占式 start 子(驱逐占住者后 start)
ROUTINE_GET_RUNNING = "routine.get_running"  # py(dial-out)->kernel: 查 running 实例(带 req_id,kernel 回 get_running_reply)
ROUTINE_GET_RUNNING_REPLY = "routine.get_running_reply"  # kernel->py: get_running 回执(带 req_id + routines [{name,id}])
ROUTINE_GET_MODULE_TREE = "routine.get_module_tree"  # py(dial-out)->kernel: 拉 module.tree(带 req_id,kernel 回 get_module_tree_reply)
ROUTINE_GET_MODULE_TREE_REPLY = "routine.get_module_tree_reply"  # kernel->py: get_module_tree 回执(带 req_id + tree)
ROUTINE_LOAD_MODULE = "routine.load_module"  # py->kernel: 往父模块加载子模块(带 req_id+parent_id+child_id,kernel 回 module_loaded)
ROUTINE_MODULE_LOADED = "routine.module_loaded"  # kernel->py: load_module 回执(带 req_id+ok+error)
ROUTINE_UNLOAD_MODULE = "routine.unload_module"  # py->kernel: 卸载子模块(带 req_id+child_id,kernel 回 module_unloaded)
ROUTINE_MODULE_UNLOADED = "routine.module_unloaded"  # kernel->py: unload_module 回执(带 req_id+ok+error)

# kernel→server 推模块树拓扑(静态 config,运行期不变).server 缓存后本地算
# cone/conflict----业务侧编排策略(如 AutoSP 自动串并行)用.
MODULE_TREE = "module.tree"

# routine→kernel catalog 推送(dial-in 专用,走 Stream).
# dial-out 下 kernel 用 Req(get_modules/get_routines) 拉;dial-in 方向矛盾,改由
# routine 连上后主动 push 一次 catalog(routines + modules),kernel 收到注册路由表 +
# 回推 module.tree.payload: {routines: [...], modules: [...]}.
CATALOG_PUSH = "catalog.push"

# catalog 增量变更(运行时 register/reload/deregister 单条同步,走 Stream).
# 三层语义:register 加一条(同名 fail),reload 覆盖一条(不区分 conn),deregister 删一条.
# 配合 catalog.push 全量(重连首帧)用----运行期变更走单条,即时精确,不用重推全量.
#
# 带 req_id + 回执(catalog.registered / catalog.reloaded / catalog.deregistered):
# kernel 是唯一真理源,py 等 kernel 回执 ok=true 才本地 Routines 操作;ok=false
# (register 同名冲突 / deregister name 不存在)抛 RegisterError / DeregisterError,
# 本地不动.reload 总 ok=true(覆盖语义,不冲突).对称 reg/reload/dereg 流程.
#
# deregister 走两跳(knowledge of holder):kernel 不直接删路由,而是先发
# catalog.deregister.cmd 给持有者 hub → hub 本地 dereg → 回执 catalog.deregister.cmd.ack
# → kernel 删路由 + 回执请求者 catalog.deregistered.支持跨 hub dereg(请求者 ≠ 持有者).
# req_id 贯穿整个流程(请求者→kernel→持有者→kernel→请求者).
CATALOG_REGISTER = "catalog.register"              # py→kernel: {req_id, name, is_passive, meta} 同名 fail
CATALOG_RELOAD = "catalog.reload"                  # py→kernel: {req_id, name, is_passive, meta} 不区分 conn 覆盖
CATALOG_DEREGISTER = "catalog.deregister"          # py→kernel: {req_id, name} 请求移除
CATALOG_DEREGISTER_CMD = "catalog.deregister.cmd"  # kernel→py: {req_id, name} 通知持有者本地 dereg
CATALOG_DEREGISTER_CMD_ACK = "catalog.deregister.cmd.ack"  # py→kernel: {req_id, ok, error?} 持有者回执
CATALOG_REGISTERED = "catalog.registered"          # kernel→py: {req_id, ok, error?}
CATALOG_RELOADED = "catalog.reloaded"              # kernel→py: {req_id, ok, error?}
CATALOG_DEREGISTERED = "catalog.deregistered"      # kernel→py: {req_id, ok, error?}
CATALOG_PUSHED = "catalog.pushed"                  # kernel→py: {req_id, registered[], skipped[]} 全量 push 回执

# envelope 保留字段名(放进 p2p.data 里,对 kernel 透明).
ENVELOPE_REQ_ID = "__req_id__"
ENVELOPE_REPLY_TO = "__reply_to__"
ENVELOPE_STREAM_ID = "__stream_id__"
ENVELOPE_EVENT = "event"          # 业务事件名(@request/@stream 的 key)
ENVELOPE_DATA = "data"            # 业务 payload
ENVELOPE_OK = "ok"
ENVELOPE_ERROR = "error"
ENVELOPE_CHUNK = "chunk"
ENVELOPE_EOF = "__eof__"          # 值:done / error / cancelled
ENVELOPE_CANCEL = "__cancel__"
