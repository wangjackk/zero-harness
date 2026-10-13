/** routine 错误类型(与 z2/errors.py 对齐). */

/** withTimeout 超时基类(区分于业务异常). */
export class TimeoutErr extends Error {}

export class ReqTimeout extends TimeoutErr {}

export class ReqError extends Error {}

export class StreamError extends Error {}

export class StreamCancelled extends Error {}

export class StreamTimeout extends TimeoutErr {}

/** start/try_start 返回的错误对象(None/null=成功).模块冲突是正常业务情况,
 * 不当异常抛:start/tryStart 返回 `StartError | null`. */
export class StartError extends Error {}

export class CreateError extends Error {}

export class AcquireError extends Error {}

export class ReleaseError extends Error {}

export class LoadModuleError extends Error {}

export class UnloadModuleError extends Error {}

export class RegisterError extends Error {}

export class ReloadError extends Error {}

export class DeregisterError extends Error {}
