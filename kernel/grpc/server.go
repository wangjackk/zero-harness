package grpc

import (
	"context"
	"fmt"
	"net"
	"strconv"
	"sync"
	"sync/atomic"

	"google.golang.org/grpc"

	"kernel/bus"
	"kernel/conn"
	"kernel/logger"
)

// Server kernel 对外的 grpc server(dial-in 模型):routine 进程主动 dial 进来,
// 每条 Stream = 一条 conn = 一个 *ServerConn.
//
// 跟 *Client(dial-out)对称:两者都实现 conn.Conn 接口(仅 ID/Req/Close/WaitReady),
// 上层(Manager)不区分方向.入站走 bus(reader Recv→bus.Publish conn.event),
// 出站走 bus(上层 publish conn.out.<id>,sendLoop 订阅调 stream.Send).conn 生命
// 周期经 bus(accept→conn.TopicConn{up},Recv err→conn.TopicConn{down}).
//
// 断线被动感知:routine 进程断开 → Stream.Recv 返回 err,无需主动探测
// (dial-out 的 *Client 要 monitorConnect 轮询 connectivity 状态机).
//
// Req(unary)方向:dial-in 下 routine 是 grpc client,kernel 是 server.routine
// 发起的 Req 由 kernel server 端回答(server.Req,现支持 get_module_tree).
// kernel→routine 的 catalog 拉取走 Stream 事件(catalog.push 由 routine 主动发),
// 不走 Req----避免 kernel 既是 server 又要当 client 调对端 Req 的方向矛盾.
// module.tree 双路:routine 连上时 Req 拉(sync 缓存,解决竞态),kernel 后续可
// push 刷新(dial-in sendOut / dial-out Req,dispatch_inbound 收).

// serverConnIDCounter dial-in conn id 自增,加 "s" 前缀跟 dial-out 区分(避免
// 路由表碰撞).第一个 = "s1",第二个 = "s2".
var serverConnIDCounter uint64

// ReqHandler dial-in routine->kernel Req 查询回调:msg(event+字段) -> reply map.
// Manager 经 SetReqHandler 注入,集中所有 Req 查询分发(get_module_tree /
// get_running_routines),访问 nodes 等 shell 层状态.返回 map 由 grpc 层转 Frame.
type ReqHandler func(map[string]any) (map[string]any, error)

// Server kernel grpc server,accept routine 进程的 dial-in 连接.
type Server struct {
	grpc       *grpc.Server
	listener   net.Listener
	onAccept   func(conn.Conn) // 每条新 Stream 接受时调(Manager 用它 AddConn)
	reqHandler ReqHandler     // dial-in routine->kernel Req 查询(Manager 注入;nil 时 Req 返 unknown event)
}

// NewServer 在 addr 上起 grpc server.onAccept 是每条新 conn 接受时的回调
// (Manager 注册 conn 进 m.conns).Start 后阻塞 accept.
func NewServer(addr string, onAccept func(conn.Conn)) (*Server, error) {
	lis, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, fmt.Errorf("listen %s: %w", addr, err)
	}
	s := &Server{
		grpc:     grpc.NewServer(),
		listener: lis,
		onAccept: onAccept,
	}
	RegisterRoutineServiceServer(s.grpc, &grpcServerImpl{server: s})
	return s, nil
}

// SetReqHandler 注入 dial-in routine->kernel Req 查询处理器(Manager 提供,
// 访问 nodes 等 shell 层状态).
func (s *Server) SetReqHandler(h ReqHandler) { s.reqHandler = h }

// Start 阻塞 accept(在调用方 goroutine).返回时 server 已停.
func (s *Server) Start() error {
	return s.grpc.Serve(s.listener)
}

// Address 返回监听地址(实际 bind 的,lis.Addr()).
func (s *Server) Address() string { return s.listener.Addr().String() }

// Close 停 server(grpc.GracefulStop).
func (s *Server) Close() error {
	s.grpc.GracefulStop()
	return nil
}

// grpcServerImpl RoutineServiceServer 实现.
type grpcServerImpl struct {
	server *Server
	UnimplementedRoutineServiceServer
}

// Req routine 发起的查询(routine→kernel).dial-in 下 routine 是 grpc client,
// 主动 Req 拉 kernel 信息(方向跟 dial-out 的 kernel→routine Req 相反).
// Req 处理 dial-in routine->kernel Req 查询:纯委托给注入的 reqHandler(shell
// Manager.HandleReq).grpc 包不持域知识--所有查询分发在 shell 层(get_module_tree /
// get_running_routines).reqHandler 未注入时返 unknown event(仅裸测场景).
func (g *grpcServerImpl) Req(ctx context.Context, in *Frame) (*Frame, error) {
	if g.server.reqHandler == nil {
		return mapToFrame(map[string]any{"error": "unknown event"})
	}
	msg, err := frameToMap(in)
	if err != nil {
		return nil, err
	}
	resp, err := g.server.reqHandler(msg)
	if err != nil {
		return nil, err
	}
	return mapToFrame(resp)
}


// Stream 接受一条 dial-in 连接:建 ServerConn → 订阅出站 topic(onAccept 前,避免
// 上层一接管就 publish 出站撞上 sub 未建→丢)→ onAccept 注册 → publish conn.up →
// 起 sendLoop(消费出站)+ readLoop(入站→bus).Recv err(routine 断开)→ publish
// conn.down,返回.
func (g *grpcServerImpl) Stream(stream RoutineService_StreamServer) error {
	sc := newServerConn(stream)
	sub := bus.GetBus().Subscribe(conn.TopicOut+"."+sc.id, 256, false)
	if g.server.onAccept != nil {
		g.server.onAccept(sc)
	}
	bus.GetBus().Publish(conn.TopicConn, conn.ConnChange{Kind: "up", ConnID: sc.id, IsDialIn: true})
	go sc.sendLoop(sub)
	sc.readLoop()
	sub.Close()
	return nil
}

// ServerConn dial-in conn(routine 进程 dial 进 kernel server 的一条 Stream).
// 实现 conn.Conn 接口,跟 *Client 对称.future chan 不在此(在 shell.node)----
// conn 零 future 知识:入站纯 publish,出站 sendLoop.
type ServerConn struct {
	id     string
	stream RoutineService_StreamServer

	// stream 接受即就绪,streamReady 立即 close(dial-in 无重连握手).
	streamMu    sync.Mutex
	stateMu     sync.Mutex
	streamReady chan struct{}

	closed bool
}

// 编译期断言:*ServerConn 实现 Conn.
var _ conn.Conn = (*ServerConn)(nil)

func newServerConn(stream RoutineService_StreamServer) *ServerConn {
	id := "s" + strconv.FormatUint(atomic.AddUint64(&serverConnIDCounter, 1), 10)
	c := &ServerConn{
		id:          id,
		stream:      stream,
		streamReady: make(chan struct{}),
	}
	close(c.streamReady)
	return c
}

func (c *ServerConn) ID() string { return c.id }

// DialIn dial-in conn(routine→kernel),catalog 走 push 不走 Req.
func (c *ServerConn) DialIn() bool { return true }

// Req dial-in 不支持 kernel→routine Req(方向矛盾).
func (c *ServerConn) Req(ctx context.Context, msg map[string]any) (map[string]any, error) {
	return nil, conn.ErrDialInNoReq
}

func (c *ServerConn) Close() error {
	c.stateMu.Lock()
	c.closed = true
	c.stateMu.Unlock()
	// server 端没有显式 Close stream API----置 closed 标志,reader Recv 会因 client
	// 断开而 err(或 grpc server GracefulStop 时所有 stream 结束).
	return nil
}

// WaitReady dial-in 接受即就绪.
func (c *ServerConn) WaitReady() bool { return true }

// readLoop 入站 reader:Recv→bus.Publish.纯传输,零 future/零业务知识.
// Recv err → onStreamError.
func (c *ServerConn) readLoop() {
	for {
		m, err := c.stream.Recv()
		if err != nil {
			c.onStreamError()
			return
		}
		msg, err := frameToMap(m)
		if err != nil {
			// 坏帧跳过(报错暴露),连接本身还活着
			logger.GetLogger().Named("rpc").Errorf("conn %s bad frame: %v", c.id, err)
			continue
		}
		bus.GetBus().Publish(conn.TopicEvent, conn.EventIn{ConnID: c.id, Msg: msg})
	}
}

// onStreamError routine 进程断开(Recv err).publish conn.down(Manager 接管
// failPending + UnloadRemote).主动 Close 触发的(closed=true)不 publish.
func (c *ServerConn) onStreamError() {
	c.stateMu.Lock()
	if c.closed {
		c.stateMu.Unlock()
		return
	}
	c.stateMu.Unlock()
	bus.GetBus().Publish(conn.TopicConn, conn.ConnChange{Kind: "down", ConnID: c.id, IsDialIn: true})
}

// sendLoop 消费出站 topic(sub 在 Stream accept 时已建好,避免 onAccept 后上层立刻
// publish 撞上 sub 未建→丢).逐条调 send 发到 wire,失败 publish conn.outfail.
// readLoop 退出(Recv err)时 Stream 调 sub.Close 让本 loop 退出.
func (c *ServerConn) sendLoop(sub *bus.Subscriber) {
	for payload := range sub.Recv() {
		out, ok := payload.(conn.OutMsg)
		if !ok {
			continue
		}
		if err := c.send(out.Msg); err != nil {
			c.publishOutFail(out.Msg)
		}
	}
}

func (c *ServerConn) publishOutFail(msg map[string]any) {
	id, _ := msg["id"].(string)
	ev, _ := msg["event"].(string)
	bus.GetBus().Publish(conn.TopicOutFail, conn.OutFail{ConnID: c.id, ID: id, Event: ev})
}

// send 出站:mapToFrame + stream.Send(加 streamMu 保护并发 Send).
func (c *ServerConn) send(msg map[string]any) error {
	f, err := mapToFrame(msg)
	if err != nil {
		return err
	}
	c.streamMu.Lock()
	defer c.streamMu.Unlock()
	return c.stream.Send(f)
}
