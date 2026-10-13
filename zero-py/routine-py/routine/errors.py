"""routine 错误类型(与 routine-py errors 对齐,精简到 in-proc 用到的子集)."""
from __future__ import annotations


class ReqTimeout(TimeoutError):
    """``req()`` 等待回执超时."""


class ReqError(Exception):
    """``req()`` 的对端 handler 抛异常(或回执 ok=false)."""


class StreamError(Exception):
    """``stream_req()`` 对端开流/产数据时出错."""


class StreamCancelled(Exception):
    """``stream_req()`` 被取消."""


class StreamTimeout(TimeoutError):
    """``stream_req()`` 开流握手超时(首帧未到)."""


class StartError(Exception):
    """``handle.start()`` 失败(kernel 拒了 start:模块冲突/父未 started).

    不当异常抛:start/try_start 返回 ``Optional[StartError]``,None=成功.
    模块冲突是正常业务情况,不该打断调用方 run() 体.
    """


class CreateError(Exception):
    """``ctx.create()`` 失败:kernel 拒了 create(routine.created 带 error)."""


class AcquireError(Exception):
    """``ctx.acquire()`` 失败(模块被占且档位不足/驱逐后仍被抢)."""


class ReleaseError(Exception):
    """``ctx.release()`` 失败(罕见)."""


class LoadModuleError(Exception):
    """``ctx.load_module()`` 失败(child 已存在 / parent 不存在)."""


class UnloadModuleError(Exception):
    """``ctx.unload_module()`` 失败(有子 / 被占 / 不存在)."""


class RegisterError(Exception):
    """运行时注册失败(同名冲突)."""


class ReloadError(Exception):
    """运行时重载失败(罕见,name 为空等参数错)."""


class DeregisterError(Exception):
    """运行时移除失败(name 不在路由表)."""
