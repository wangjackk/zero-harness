"""routine wire 协议常量 + Frame 编解码(wire 契约: kernel/routine.proto, 无 grpc 依赖).

in-proc 传输下消息就是 dict 本体(零序列化);常量层保持与 wire 协议同名同义,
grpc transport 时消息走 Frame{payload=JSON 文本}(kernel 侧 server 与 hub 侧
client 共用本编解码, wire 与 routine.proto 契约完全一致).
"""
from __future__ import annotations

from enum import Enum


class ControlDoneReason(str, Enum):
    """lifecycle.stopped 回报里的 reason 取值(wire 大写)."""

    UNKNOWN = 'UNKNOWN'
    AUTO = 'AUTO'
    STOP = 'STOP'
    UNSUBMITTED = 'UNSUBMITTED'
    ERROR = 'ERROR'
    CANCEL = 'CANCEL'
    FORCE = 'FORCE'
    DISCONNECT = 'DISCONNECT'


# --- lifecycle(kernel ↔ hub)---
LIFECYCLE_CREATED = 'lifecycle.created'      # kernel→hub 实例化;hub→kernel created 回报(带 modules)
LIFECYCLE_START = 'lifecycle.start'          # kernel→hub 启动 routine
LIFECYCLE_STOP = 'lifecycle.stop'            # kernel→hub 打断 routine(带 reason=force/by 时为驱逐)
LIFECYCLE_DESTROY = 'lifecycle.destroy'      # kernel→hub 销毁 created 态 routine
LIFECYCLE_STARTED = 'lifecycle.started'      # hub→kernel 已启动
LIFECYCLE_STOPPED = 'lifecycle.stopped'      # hub→kernel 已停止(带 reason/result/error)

# --- routine 调 kernel 请求/回执(hub→kernel→hub)---
ROUTINE_SUBMIT = 'routine.submit'
ROUTINE_SUBMITTED = 'routine.submitted'
ROUTINE_REJECTED = 'routine.rejected'
ROUTINE_START = 'routine.start'      # 带可选 priority(int, 默认 0): 冲突时
                                      # incoming > holder 才驱逐后 start
ROUTINE_STOP = 'routine.stop'
ROUTINE_UNSUBMIT = 'routine.unsubmit'
ROUTINE_ACQUIRE = 'routine.acquire'
ROUTINE_ACQUIRED = 'routine.acquired'
ROUTINE_RELEASE = 'routine.release'
ROUTINE_RELEASED = 'routine.released'
ROUTINE_LOAD_MODULE = 'routine.load_module'
ROUTINE_MODULE_LOADED = 'routine.module_loaded'
ROUTINE_UNLOAD_MODULE = 'routine.unload_module'
ROUTINE_MODULE_UNLOADED = 'routine.module_unloaded'

# --- message.*(kernel dumb forward by target_id)---
MESSAGE_SEND = 'message.send'
MESSAGE_DELIVERED = 'message.delivered'
MESSAGE_REQ = 'message.req'
MESSAGE_REQ_DELIVERED = 'message.req_delivered'
MESSAGE_REQ_REPLY = 'message.req_reply'
MESSAGE_REQ_REPLY_DELIVERED = 'message.req_reply_delivered'
MESSAGE_STREAM_OPEN = 'message.stream_open'
MESSAGE_STREAM_OPEN_DELIVERED = 'message.stream_open_delivered'
MESSAGE_STREAM_DATA = 'message.stream_data'
MESSAGE_STREAM_DATA_DELIVERED = 'message.stream_data_delivered'
MESSAGE_STREAM_CANCEL = 'message.stream_cancel'
MESSAGE_STREAM_CANCEL_DELIVERED = 'message.stream_cancel_delivered'

# --- pubsub(kernel 维护订阅表 fanout)---
PUBSUB_SUBSCRIBE = 'pubsub.subscribe'
PUBSUB_UNSUBSCRIBE = 'pubsub.unsubscribe'
PUBSUB_PUBLISH = 'pubsub.publish'
PUBSUB_DELIVERED = 'pubsub.delivered'

# --- lifecycle 观察流(conn 级观察者, kernel 内 routine 树状态变更的原始广播)---
# 与 pubsub 的分野: pubsub 是业务 topic 寻址的 routine 间通信; lifecycle 是框架
# 可观察性原语 ---- kernel 只知道"哪些 conn 开了观察", 不感知消费者语义.
LIFECYCLE_WATCH = 'lifecycle.watch'      # conn→kernel: 开启观察(幂等, 带 req_id)
LIFECYCLE_UNWATCH = 'lifecycle.unwatch'    # conn→kernel: 关闭观察
LIFECYCLE_SNAPSHOT = 'lifecycle.snapshot'  # kernel→conn: enable 回执(当前树快照, 带 req_id+seq)
LIFECYCLE_EVENT = 'lifecycle.event'                    # kernel→conn: 事件帧(type=created/started/stopped/destroyed)

# --- yield(child→kernel→parent,kernel dumb forward)---
ROUTINE_YIELD = 'routine.yield'
ROUTINE_YIELDED = 'routine.yielded'

# --- module tree(kernel→hub 推送拓扑)---
MODULE_TREE = 'module.tree'

# --- catalog(hub→kernel 注册 / kernel 回执)---
CATALOG_PUSH = 'catalog.push'
CATALOG_PUSHED = 'catalog.pushed'
CATALOG_REGISTER = 'catalog.register'
CATALOG_REGISTERED = 'catalog.registered'
CATALOG_RELOAD = 'catalog.reload'
CATALOG_RELOADED = 'catalog.reloaded'
CATALOG_DEREGISTER = 'catalog.deregister'
CATALOG_DEREGISTERED = 'catalog.deregistered'
CATALOG_DEREGISTER_CMD = 'catalog.deregister.cmd'
CATALOG_DEREGISTER_CMD_ACK = 'catalog.deregister.cmd.ack'

# --- Req 查询事件(hub→kernel 同步直达)---
REQ_GET_RUNNING = 'get_running_routines'
REQ_GET_MODULE_TREE = 'get_module_tree'
REQ_GET_ROUTINES = 'get_routines'

# --- envelope 保留字段(message.* 的 data 内,kernel 透明)---
ENVELOPE_REQ_ID = '__req_id__'
ENVELOPE_REPLY_TO = '__reply_to__'
ENVELOPE_STREAM_ID = '__stream_id__'
ENVELOPE_EVENT = 'event'
ENVELOPE_DATA = 'data'
ENVELOPE_OK = 'ok'
ENVELOPE_ERROR = 'error'
ENVELOPE_CHUNK = 'chunk'
ENVELOPE_EOF = '__eof__'      # 值: done / error / cancelled
ENVELOPE_CANCEL = '__cancel__'


# --- Frame{payload=1: string} 手写 protobuf 编解码(二进制 wire 格式)---
# 与 routine.proto 生成的 pb2 二进制兼容;手写免 protobuf 依赖, kernel server 与
# hub client 共用.

def frame_encode(payload: str) -> bytes:
    """str payload → protobuf wire(field 1, tag 0x0a + varint len + utf8)."""
    body = payload.encode('utf-8')
    n = len(body)
    varint = bytearray()
    while n >= 0x80:
        varint.append((n & 0x7f) | 0x80)
        n >>= 7
    varint.append(n)
    return b'\x0a' + bytes(varint) + body


def frame_decode(buf: bytes) -> str:
    """protobuf wire → str payload.格式违规当场抛(wire 级损坏由 gRPC 层断言)."""
    if not buf or buf[0] != 0x0a:
        tag = buf[0] if buf else 'empty'
        raise ValueError(f'bad protobuf tag {tag}')
    length = 0
    shift = 0
    i = 1
    while True:
        if i >= len(buf):
            raise ValueError('truncated varint')
        b = buf[i]
        i += 1
        length |= (b & 0x7f) << shift
        if not (b & 0x80):
            break
        shift += 7
    payload = buf[i:i + length]
    if len(payload) != length:
        raise ValueError('truncated payload')
    return payload.decode('utf-8')
