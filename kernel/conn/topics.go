package conn

// bus topic 常量 + 载荷类型.
//
// kernel/bus 是通用 pub/sub(payload any,不认识域类型);topic 名与载荷由 conn 域
// 定义在此----dial-out 和 dial-in 的 reader 都往同一条 bus publish 这两类载荷,
// 上层订阅一次覆盖两种来源.
//
// up/down 合一 topic 单 goroutine FIFO 消费----保证 conn.down 的 UnloadRemote +
// pushModuleView 在下次 conn.up 的 LoadCatalog 前完成,避免 stale 视图覆盖.
const (
	TopicEvent   = "conn.event"   // 入站事件 topic,载荷 EventIn
	TopicConn    = "conn.conn"    // conn 生命周期 topic,载荷 ConnChange
	TopicOut     = "conn.out"     // 出站命令 topic 前缀,实际 = TopicOut + "." + connID,载荷 OutMsg
	TopicOutFail = "conn.outfail" // 出站 send 失败 topic(单 topic),载荷 OutFail
)

// EventIn 入站事件载荷:哪条 conn 收到的 msg.
type EventIn struct {
	ConnID string
	Msg    map[string]any
}

// ConnChange conn 生命周期载荷.Kind="up"(连上,含首次+重连)/ "down"(断开).
// IsDialIn 区分方向:dial-out=false(kernel→routine,拉 catalog),dial-in=true
// (routine→kernel,收 catalog push).上层据此决定 catalog 是拉还是收.
type ConnChange struct {
	Kind     string
	ConnID   string
	IsDialIn bool
}

// OutMsg 出站命令载荷:上层(shell)要把 Msg 发到 connID 这条 conn 的 wire.
// 上层 publish 到 TopicOut+"."+connID,该 conn 的 sendLoop 订阅,调底层 stream.Send.
// fire-and-forget:sendLoop 发失败时 publish OutFail(带 Msg 里的 id/event),让上层
// 的 future waiter(node chan)经 bus 回流拿到错误----不丢,tracer 可见.
type OutMsg struct {
	Msg map[string]any
}

// OutFail 出站 send 失败载荷:connID 这条 conn 发 Msg 失败(stream 断 / 发不出).
// ID/Event 取自原 Msg(routine id + event 名),让 Manager 据此 resolve 对应 routine
// 的 future chan(created→createdCh,start/stop→stoppedCh)----调用方 select 收到 err 返回.
// 单 topic(不按 connID 拆):Manager 订阅一次覆盖所有 conn,按 ID 查 node.
type OutFail struct {
	ConnID string
	ID     string
	Event  string
}
