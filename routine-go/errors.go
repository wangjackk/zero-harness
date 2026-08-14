package routine

import "errors"

// routine↔routine 通信的错误类型.对标 Python routine/errors.py.

// ReqError req() 的对端 handler 抛异常(或回执 ok=false).
var ReqError = errors.New("req failed")

// ReqTimeout req() 等待回执超时.
var ReqTimeout = errors.New("req timeout")

// StartError handle.start()/try_start() 失败(模块冲突/父未 started).
var StartError = errors.New("start failed")

// SubmitError ctx.submit() 失败(kernel 拒了 submit).
var SubmitError = errors.New("submit failed")

// AcquireError ctx.acquire()/force_acquire() 失败(acquired ok=false).
var AcquireError = errors.New("acquire failed")

// ReleaseError ctx.release()/force_release() 失败(released ok=false).
var ReleaseError = errors.New("release failed")

// LoadModuleError ctx.load_module() 失败.
var LoadModuleError = errors.New("load_module failed")

// UnloadModuleError ctx.unload_module() 失败.
var UnloadModuleError = errors.New("unload_module failed")

// RegisterError RoutineHub.RegisterRoutine() 失败(同名冲突).
var RegisterError = errors.New("register rejected by kernel")

// ReloadError RoutineHub.ReloadRoutine() 失败.
var ReloadError = errors.New("reload rejected by kernel")

// DeregisterError RoutineHub.DeregisterRoutine() 失败.
var DeregisterError = errors.New("deregister rejected by kernel")

// StreamError stream_req() 对端开流/产数据时出错.
var StreamError = errors.New("stream provider error")

// StreamCancelled stream_req() 被取消.
var StreamCancelled = errors.New("stream cancelled")

// StreamTimeout stream_req() 开流握手超时.
var StreamTimeout = errors.New("stream open handshake timeout")

// errType 按名称查 sentinel error,供 resolveAckFuture 复用.
func errType(name string) error {
	switch name {
	case "acquire":
		return AcquireError
	case "release":
		return ReleaseError
	case "load_module":
		return LoadModuleError
	case "unload_module":
		return UnloadModuleError
	case "register":
		return RegisterError
	case "reload":
		return ReloadError
	case "deregister":
		return DeregisterError
	default:
		return errors.New(name + " failed")
	}
}
