"""routine↔routine 通信的错误类型(ReqTimeout / ReqError / Stream* 系列)."""
from __future__ import annotations


class ReqTimeout(TimeoutError):
    """``req()`` 等待回执超时."""


class ReqError(Exception):
    """``req()`` 的对端 handler 抛异常(或回执 ok=false)."""


class StartError(Exception):
    """``handle.start()`` / ``handle.try_start()`` 失败.

    模块冲突 / 父未 started 等--kernel 拒了 start(routine.rejected op=start).
    不当异常抛:start/try_start 返回 ``Optional[StartError]``,None=成功.
    模块冲突是正常业务情况(占住者还没释放),不该打断调用方 run() 体.
    """


class SubmitError(Exception):
    """``ctx.submit()`` 失败:kernel 拒了 submit(routine.submitted 带 error).

    罕见--通常是 name 找不到 / kwargs 不合法.跟 ``StartError`` 区分:submit 失败在
    created 阶段(建子命令前),start 失败在 start 阶段(占模块时).对齐 ``ReqError``
    的类型化:让调用方能 ``except SubmitError`` 而非 string match RuntimeError.
    """


class AcquireError(Exception):
    """``ctx.acquire()`` / ``ctx.force_acquire()`` 失败:routine.acquired ok=false.

    acquire 冲突(模块被第三方占住);force_acquire 驱逐后仍冲突(竞态,被别人抢了).
    冲突是业务高频分支(占住者未释放属预期),值得类型化捕获:``except AcquireError``
    而非 string match.跟 ``StartError`` 区别:start 也会撞冲突但 start 走 rejected 返
    StartError(保留可重试);acquire/force_acquire 是运行时占领,失败直接抛.
    """


class ReleaseError(Exception):
    """``ctx.release()`` / ``ctx.force_release()`` 失败:routine.released ok=false.

    release 一般不冲突(自己占的自己释放);force_release 只驱逐不占,基本总成功
    (失败罕见--rid 未 started).跟 AcquireError 对称类型化:force_release 失败走此类
    (wire 上 force_release ack 复用 routine.released).
    """


class LoadModuleError(Exception):
    """``ctx.load_module()`` 失败:routine.module_loaded ok=false.

    常见:child_id 已存在 / parent_id 不存在.load 只挂树不占用,失败是状态错而非冲突.
    """


class UnloadModuleError(Exception):
    """``ctx.unload_module()`` 失败:routine.module_unloaded ok=false.

    常见:module 有子模块未 unload / 被占用未 release / 不存在.
    """


class RegisterError(Exception):
    """``RoutineHub.register_routine()`` 失败:catalog.registered ok=false.

    常见:同名冲突(不区分 conn----无论同 conn 还是跨 conn,name 已存在就拒绝).
    kernel 拒绝后者(先到先得),py 本地 Routines 不 register(保持跟 kernel 路由表一致).
    覆盖语义走 ``reload_routine``(不区分 conn 覆盖).对称 DeregisterError / ReloadError.
    """


class ReloadError(Exception):
    """``RoutineHub.reload_routine()`` 失败:catalog.reloaded ok=false.

    罕见----reload 是覆盖语义,不区分 conn,同名总 ok=true.失败只发生在 name 为空
    等参数错场景.py 本地 Routines 不 register(保持跟 kernel 一致).对称
    RegisterError / DeregisterError.
    """


class DeregisterError(Exception):
    """``RoutineHub.deregister_routine()`` 失败:catalog.deregistered ok=false.

    常见:name 不在 kernel 路由表(从未注册 / 已被其他流程清掉 / 跨 conn 误删).
    py 本地 Routines 不 deregister(保持跟 kernel 一致).对称 RegisterError / ReloadError.
    """


class StreamError(Exception):
    """``stream_req()`` 对端开流/产数据时出错."""


class StreamCancelled(Exception):
    """``stream_req()`` 被取消(消费方主动 cancel 或对端取消)."""


class StreamTimeout(TimeoutError):
    """``stream_req()`` 开流握手超时(首帧未到)."""
