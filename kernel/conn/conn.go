// Package conn kernel 与一条 routine 进程之间的连接契约,方向无关,传输无关.
//
// 两种实现(都在 kernel/grpc):
//   - *Client(dial-out):kernel 主动 dial 远端 routine server(现有模型)
//   - *ServerConn(dial-in):routine 进程主动 dial kernel server(新增模型)
//
// 上层(shell.Manager)持 map[string]Conn,按 name 路由定 connID.
//
// 入站(routine→kernel):reader 纯 publish 到 bus(TopicEvent)----conn 零业务/零 future
// 知识.Manager 订阅 dispatch + resolve routine 的 future chan(chan 在 node 上,不在 conn).
//
// 出站(kernel→routine):上层 publish 到 bus 的 per-conn topic(TopicOut+"."+connID),
// 该 conn 的 sendLoop 订阅,调底层 stream.Send.send 失败 publish OutFail(TopicOutFail),
// Manager 据此 resolve future chan with err----错误经 bus 回流,不丢,tracer 可见.
// 出站全走 bus 是为跟入站对称(一个心智模型)+ tracer 能看到出站命令.
//
// Req(unary 查询)保持直连:本就是 sync req/reply,不走 Stream 出站总线.dial-in
// 不支持 kernel→routine Req(方向矛盾,返回 ErrDialInNoReq),1b push 协议补上后不再需要.
//
// 本包还定义 bus 的 topic 名与载荷(EventIn / ConnChange / OutMsg / OutFail),
// wire 协议词汇(事件 const),dict helper----都是传输无关的契约,供 grpc 实现层 +
// shell 编排层共享.future 回执类型 CreatedResult 留在此(node 持 chan 用).
package conn

import (
	"context"
	"errors"
	"time"
)

// ErrDialInNoReq dial-in conn 不支持 kernel→routine 的 Req(方向矛盾,走 Stream).
// (1b routine push 协议补上后不再需要.)
var ErrDialInNoReq = errors.New("dial-in conn does not support kernel→routine Req; use Stream events")

// CreatedTimeout 等 server 的 created 回报最长时间.server handle_created 跑实例化+
// register+auto_subscribe(本地+一次 pubsub.subscribe 发送),正常秒内完成.
const CreatedTimeout = 10 * time.Second

// CreatedResult created 回报结果:err=nil 表示成功,modules 是 created() 返回值
// (static=固定 list,dynamic=kwargs 现算).node.createdCh 带这个,让 runRemote /
// handleReverseSubmit 直接拿到 modules 存进 node.declared----去掉 OnCreatedModules /
// OnSubmitCreatedModules 两个 handler 方法(modules 经 chan 流转,不经 reader 回调存).
type CreatedResult struct {
	Err     error
	Modules []string
}

// Conn 方向无关的连接接口.只保留传输层最小契约:标识 + 查询 + 关闭 + 就绪.
//
// 出站命令经 bus(上层 publish 到 TopicOut+connID,conn 的 sendLoop 订阅发送)----
// 不在接口上.future 回执在 node 上(不在 conn)----不在接口上.lifecycle 消息构建
// 在 shell(sendCreated/sendStart/sendStop helper)----不在接口上.
//
// DialIn 区分方向:dial-out=false(*Client,kernel→routine,catalog 走 Req 拉),
// dial-in=true(*ServerConn,routine→kernel,catalog 走 push).pushModuleView 据此
// 决定 module.tree 走同步 Req(dial-out)还是 fire-and-forget Stream 事件(dial-in).
type Conn interface {
	ID() string
	Req(ctx context.Context, msg map[string]any) (map[string]any, error)
	Close() error
	WaitReady() bool
	DialIn() bool
}
