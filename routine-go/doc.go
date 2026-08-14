// Package routine 是 kshell routine SDK 的 Go 版本.
//
// 对标 Python routine SDK:routine 体由本 SDK 的 server 实例化运行;调度器(kernel)
// 通过 gRPC lifecycle 事件驱动 create/start/stop,并 dumb-forward p2p 帧.
// 通信(req/streamreq 骑 p2p 隧道,kernel dumb forward)经 HandleRequest/HandleStream
// + RunContext.Req/StreamReq 暴露.
//
// Go 适配:接口替代继承,channel 替代 asyncio Future,显式注册替代装饰器,
// ctx.Yield(data) 对齐 Python async generator 的 yield item.
package routine

// Routine 业务入口方法名是 Run(对齐 Python async def run(self, kwargs)),
// 不是 Start.Start 是 lifecycle 概念(kernel 管 routine 实例状态机 create/start/stop/destroy).
