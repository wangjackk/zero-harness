package routine

import (
	"context"
	"fmt"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/peer"
	"google.golang.org/protobuf/types/known/structpb"
)

// RoutineServiceName service 名(对标 proto 中的 "routine.RoutineService").
const RoutineServiceName = "routine.RoutineService"

// RoutineReqMethod / RoutineStreamMethod fully-qualified 方法名.
const (
	RoutineReqMethod    = "/" + RoutineServiceName + "/Req"
	RoutineStreamMethod = "/" + RoutineServiceName + "/Stream"
)

// routineServiceDesc 手写 ServiceDesc,免去 protoc 生成.
// wire schema:Req(Struct) returns (Struct);Stream(stream Struct) returns (stream Struct).
var routineServiceDesc = grpc.ServiceDesc{
	ServiceName: RoutineServiceName,
	HandlerType: (*RoutineServiceServer)(nil),
	Methods: []grpc.MethodDesc{
		{Handler: routineReqHandler, MethodName: "Req"},
	},
	Streams: []grpc.StreamDesc{
		{Handler: routineStreamHandler, StreamName: "Stream", ServerStreams: true, ClientStreams: true},
	},
	Metadata: "routine.proto",
}

// RoutineServiceServer service 接口(供 GrpcServerTransport 实现).
type RoutineServiceServer interface {
	Req(ctx context.Context, req *structpb.Struct) (*structpb.Struct, error)
	Stream(stream grpc.BidiStreamingServer[structpb.Struct, structpb.Struct]) error
}

// --- gRPC handler 桥(把 any 转回具体类型) ---

// 由于 grpc.ServiceDesc.Methods[].Handler 签名是 func(srv any, ctx context.Context,
// dec func(any) error, interceptor grpc.UnaryServerInterceptor) (any, error),
// 需要桥接回 structpb.Struct.

func routineReqHandler(srv any, ctx context.Context, dec func(any) error, interceptor grpc.UnaryServerInterceptor) (any, error) {
	in := new(structpb.Struct)
	if err := dec(in); err != nil {
		return nil, err
	}
	if interceptor == nil {
		return srv.(RoutineServiceServer).Req(ctx, in)
	}
	info := &grpc.UnaryServerInfo{Server: srv, FullMethod: RoutineReqMethod}
	handler := func(ctx context.Context, req any) (any, error) {
		return srv.(RoutineServiceServer).Req(ctx, req.(*structpb.Struct))
	}
	return interceptor(ctx, in, info, handler)
}

func routineStreamHandler(srv any, stream grpc.ServerStream) error {
	wrapped := &grpcBidiServer{ServerStream: stream}
	return srv.(RoutineServiceServer).Stream(wrapped)
}

// grpcBidiServer 适配 grpc.ServerStream → grpc.BidiStreamingServer[Struct, Struct].
type grpcBidiServer struct {
	grpc.ServerStream
}

func (s *grpcBidiServer) Send(m *structpb.Struct) error {
	return s.ServerStream.SendMsg(m)
}

func (s *grpcBidiServer) Recv() (*structpb.Struct, error) {
	m := new(structpb.Struct)
	if err := s.ServerStream.RecvMsg(m); err != nil {
		return nil, err
	}
	return m, nil
}

// --- RoutineServiceClient 手写 client stub(对标 protoc 生成的 RoutineServiceClient) ---

type RoutineServiceClient interface {
	Req(ctx context.Context, in *structpb.Struct, opts ...grpc.CallOption) (*structpb.Struct, error)
	Stream(ctx context.Context, opts ...grpc.CallOption) (grpc.BidiStreamingClient[structpb.Struct, structpb.Struct], error)
}

type routineServiceClient struct {
	cc *grpc.ClientConn
}

func NewRoutineServiceClient(cc *grpc.ClientConn) RoutineServiceClient {
	return &routineServiceClient{cc: cc}
}

func (c *routineServiceClient) Req(ctx context.Context, in *structpb.Struct, opts ...grpc.CallOption) (*structpb.Struct, error) {
	out := new(structpb.Struct)
	err := c.cc.Invoke(ctx, RoutineReqMethod, in, out, opts...)
	if err != nil {
		return nil, err
	}
	return out, nil
}

func (c *routineServiceClient) Stream(ctx context.Context, opts ...grpc.CallOption) (grpc.BidiStreamingClient[structpb.Struct, structpb.Struct], error) {
	stream, err := c.cc.NewStream(ctx, &routineServiceDesc.Streams[0], RoutineStreamMethod, opts...)
	if err != nil {
		return nil, err
	}
	return &grpcBidiClient{ClientStream: stream}, nil
}

type grpcBidiClient struct {
	grpc.ClientStream
}

func (c *grpcBidiClient) Send(m *structpb.Struct) error { return c.ClientStream.SendMsg(m) }
func (c *grpcBidiClient) CloseSend() error              { return c.ClientStream.CloseSend() }
func (c *grpcBidiClient) Recv() (*structpb.Struct, error) {
	m := new(structpb.Struct)
	if err := c.ClientStream.RecvMsg(m); err != nil {
		return nil, err
	}
	return m, nil
}

// ============================================================
// GrpcServerTransport dial-out:routine 当 grpc server.
// ============================================================

// GrpcServerTransport routine 作为 grpc server 监听 address.每条接入 Stream 一个 peer.
type GrpcServerTransport struct {
	address string

	hub       *RoutineHub
	server    *grpc.Server
	listener  net.Listener
	boundPort int

	mu         sync.Mutex
	outQueues  map[string]chan *structpb.Struct
	stopCh     chan struct{}
	stopOnce   sync.Once

	// dial-out get_running / get_module_tree 请求-回执 future 表.
	getRunningFutures     map[string]chan map[string]any
	getModuleTreeFutures  map[string]chan map[string]any
	futuresMu             sync.Mutex
}

// NewGrpcServerTransport 创建 dial-out server transport.
func NewGrpcServerTransport(address string) *GrpcServerTransport {
	return &GrpcServerTransport{
		address:              address,
		outQueues:            make(map[string]chan *structpb.Struct),
		stopCh:               make(chan struct{}),
		getRunningFutures:    make(map[string]chan map[string]any),
		getModuleTreeFutures: make(map[string]chan map[string]any),
	}
}

// Attach hub 建好后调:注入回调.
func (t *GrpcServerTransport) Attach(hub *RoutineHub) {
	t.hub = hub
}

// Start bind + start grpc server.
func (t *GrpcServerTransport) Start() error {
	if t.hub == nil {
		return fmt.Errorf("GrpcServerTransport.Start requires Attach(hub) first")
	}
	lis, err := net.Listen("tcp", t.address)
	if err != nil {
		return fmt.Errorf("cannot bind to %s: %w", t.address, err)
	}
	t.listener = lis
	t.boundPort = lis.Addr().(*net.TCPAddr).Port

	server := grpc.NewServer()
	t.server = server
	RegisterRoutineServiceServer(server, &grpcServerServicer{transport: t})

	t.hub.logger.Infof("routine server started: %s (port=%d)", t.address, t.boundPort)
	go func() {
		_ = server.Serve(lis)
		close(t.stopCh)
	}()
	return nil
}

// Wait 等监听退出.
func (t *GrpcServerTransport) Wait() error {
	<-t.stopCh
	return nil
}

// Stop 停 grpc server.
func (t *GrpcServerTransport) Stop() error {
	t.stopOnce.Do(func() {
		if t.server != nil {
			t.server.Stop()
		}
	})
	return nil
}

// SendEvent 出站:发事件给 peer(peerID="" 广播所有 peer).
func (t *GrpcServerTransport) SendEvent(payload map[string]any, peerID string) error {
	msg := mapToStruct(payload)
	t.mu.Lock()
	defer t.mu.Unlock()
	if peerID == "" {
		for _, q := range t.outQueues {
			select {
			case q <- msg:
			default:
				// 队列满丢弃,fire-and-forget.
			}
		}
		return nil
	}
	q, ok := t.outQueues[peerID]
	if !ok {
		return nil
	}
	select {
	case q <- msg:
	default:
	}
	return nil
}

// Req dial-out server 不支持 routine→kernel Req(方向矛盾).
func (t *GrpcServerTransport) Req(msg map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("dial-out server does not support Req")
}

// GetRunningRoutines dial-out:经 Stream 请求-回执问 kernel.
func (t *GrpcServerTransport) GetRunningRoutines() ([]map[string]any, error) {
	t.mu.Lock()
	hasPeer := len(t.outQueues) > 0
	t.mu.Unlock()
	if !hasPeer {
		return []map[string]any{}, nil
	}
	reqID := newUUID()
	fut := make(chan map[string]any, 1)
	t.futuresMu.Lock()
	t.getRunningFutures[reqID] = fut
	t.futuresMu.Unlock()
	defer func() {
		t.futuresMu.Lock()
		delete(t.getRunningFutures, reqID)
		t.futuresMu.Unlock()
	}()

	if err := t.SendEvent(map[string]any{"event": RoutineGetRunning, "req_id": reqID}, ""); err != nil {
		return nil, err
	}
	select {
	case msg := <-fut:
		routines, _ := msg["routines"].([]map[string]any)
		return routines, nil
	case <-time.After(2 * time.Second):
		return []map[string]any{}, nil
	}
}

// GetModuleTree dial-out:经 Stream 请求-回执问 kernel 拉 module.tree.
func (t *GrpcServerTransport) GetModuleTree() (*ModuleTree, error) {
	if t.hub == nil {
		return nil, nil
	}
	t.mu.Lock()
	hasPeer := len(t.outQueues) > 0
	t.mu.Unlock()
	if !hasPeer {
		return t.hub.Runtime.GetModuleTree(), nil
	}
	reqID := newUUID()
	fut := make(chan map[string]any, 1)
	t.futuresMu.Lock()
	t.getModuleTreeFutures[reqID] = fut
	t.futuresMu.Unlock()
	defer func() {
		t.futuresMu.Lock()
		delete(t.getModuleTreeFutures, reqID)
		t.futuresMu.Unlock()
	}()

	if err := t.SendEvent(map[string]any{"event": RoutineGetModuleTree, "req_id": reqID}, ""); err != nil {
		return nil, err
	}
	select {
	case msg := <-fut:
		okV, _ := msg["ok"].(bool)
		if okV {
			t.hub.Query.CacheModuleTree(msg)
		}
		return t.hub.Runtime.GetModuleTree(), nil
	case <-time.After(2 * time.Second):
		return t.hub.Runtime.GetModuleTree(), nil
	}
}

// GetRoutines dial-out 暂未实现(走 Stream 请求-回执).
func (t *GrpcServerTransport) GetRoutines() ([]map[string]any, error) {
	return nil, fmt.Errorf("dial-out server: GetRoutines not implemented")
}

// ResolveGetRunning 收 routine.get_running_reply 回执.
func (t *GrpcServerTransport) ResolveGetRunning(msg map[string]any) {
	reqID, _ := msg["req_id"].(string)
	t.futuresMu.Lock()
	fut, ok := t.getRunningFutures[reqID]
	if ok {
		delete(t.getRunningFutures, reqID)
	}
	t.futuresMu.Unlock()
	if ok {
		select {
		case fut <- msg:
		default:
		}
	}
}

// ResolveGetModuleTree 收 routine.get_module_tree_reply 回执.
func (t *GrpcServerTransport) ResolveGetModuleTree(msg map[string]any) {
	reqID, _ := msg["req_id"].(string)
	t.futuresMu.Lock()
	fut, ok := t.getModuleTreeFutures[reqID]
	if ok {
		delete(t.getModuleTreeFutures, reqID)
	}
	t.futuresMu.Unlock()
	if ok {
		select {
		case fut <- msg:
		default:
		}
	}
}

// --- servicer 实现 ---

type grpcServerServicer struct {
	transport *GrpcServerTransport
}

func (s *grpcServerServicer) Req(ctx context.Context, req *structpb.Struct) (*structpb.Struct, error) {
	if s.transport.hub == nil {
		return mapToStruct(map[string]any{"error": "no hub"}), nil
	}
	return s.transport.hub.Query.HandleReq(req), nil
}

func (s *grpcServerServicer) Stream(stream grpc.BidiStreamingServer[structpb.Struct, structpb.Struct]) error {
	peerID := peerIDFromContext(stream.Context())
	s.transport.hub.logger.Infof("🔗 [Stream] connected: %s", peerID)

	out := s.transport.ensureQueue(peerID)
	defer s.transport.dropQueue(peerID)

	streamDone := make(chan struct{})
	var once sync.Once
	closeDone := func() { once.Do(func() { close(streamDone) }) }

	// reader goroutine:收 inbound → dispatch.
	go func() {
		defer closeDone()
		for {
			msg, err := stream.Recv()
			if err != nil {
				if err != io.EOF {
					s.transport.hub.logger.Warnf("stream inbound error: %v", err)
				}
				return
			}
			m := structAsMap(msg)
			s.transport.hub.DispatchInbound(peerID, m)
		}
	}()

	// writer:消费 out 队列 → stream.Send.
	for {
		select {
		case msg := <-out:
			if msg == nil {
				return nil
			}
			if err := stream.Send(msg); err != nil {
				s.transport.hub.logger.Warnf("stream send error: %v", err)
				return err
			}
		case <-streamDone:
			return nil
		}
	}
}

func (t *GrpcServerTransport) ensureQueue(peerID string) chan *structpb.Struct {
	t.mu.Lock()
	defer t.mu.Unlock()
	q, ok := t.outQueues[peerID]
	if !ok {
		q = make(chan *structpb.Struct, 64)
		t.outQueues[peerID] = q
	}
	return q
}

func (t *GrpcServerTransport) dropQueue(peerID string) {
	t.mu.Lock()
	q, ok := t.outQueues[peerID]
	if ok {
		delete(t.outQueues, peerID)
	}
	t.mu.Unlock()

	// peer 断连:强制清理该 peer 的所有 routine instance.
	if t.hub != nil {
		t.hub.OnPeerDown(peerID)
	}
	if q != nil {
		// 不必 close(q)——grpcServerServicer.Stream 的 writer 循环会因 streamDone 退出.
		_ = q
	}
	t.hub.logger.Infof("❌ [Stream] disconnected: %s", peerID)
}

// ============================================================
// GrpcClientTransport dial-in:routine 当 grpc client.
// ============================================================

// GrpcClientTransport routine 作为 grpc client 拨 kernel server.单 peer(kernel).
type GrpcClientTransport struct {
	address string
	peerID  string

	hub *RoutineHub

	mu       sync.Mutex
	channel  *grpc.ClientConn
	stub     RoutineServiceClient
	stream   grpc.BidiStreamingClient[structpb.Struct, structpb.Struct]
	streamMu sync.Mutex

	outQ     chan map[string]any
	outQLen  int

	stopFlag  bool
	stopMu    sync.Mutex
	ready     chan struct{}
	readyOnce sync.Once

	runDone chan struct{}
}

// dial-in 常量.
const (
	ClientPeerID       = "kernel"
	BackoffInitial     = 200 * time.Millisecond
	BackoffMax         = 5 * time.Second
	BackoffFactor      = 1.5
	ClientReqTimeout   = 5 * time.Second
	ClientOutQMaxSize  = 256
	ClientStableSeconds = 2.0
)

// NewGrpcClientTransport 创建 dial-in client transport.
func NewGrpcClientTransport(address string) *GrpcClientTransport {
	return &GrpcClientTransport{
		address: address,
		peerID:  ClientPeerID,
		outQ:    make(chan map[string]any, ClientOutQMaxSize),
		ready:   make(chan struct{}),
		runDone: make(chan struct{}),
	}
}

// Attach hub 建好后调.
func (t *GrpcClientTransport) Attach(hub *RoutineHub) {
	t.hub = hub
}

// Start 起 _run loop(含重连).
func (t *GrpcClientTransport) Start() error {
	go t.run()
	return nil
}

// Wait 等 _run 退出.
func (t *GrpcClientTransport) Wait() error {
	<-t.runDone
	return nil
}

// Stop 主动停.
func (t *GrpcClientTransport) Stop() error {
	t.stopMu.Lock()
	if t.stopFlag {
		t.stopMu.Unlock()
		return nil
	}
	t.stopFlag = true
	t.stopMu.Unlock()

	t.clearReady()
	t.streamMu.Lock()
	if t.stream != nil {
		_ = t.stream.CloseSend()
	}
	t.stream = nil
	t.streamMu.Unlock()

	t.mu.Lock()
	if t.channel != nil {
		_ = t.channel.Close()
	}
	t.mu.Unlock()

	<-t.runDone
	return nil
}

// SendEvent fire-and-forget:放进有界 queue.
func (t *GrpcClientTransport) SendEvent(payload map[string]any, peerID string) error {
	t.stopMu.Lock()
	stopped := t.stopFlag
	t.stopMu.Unlock()
	if stopped {
		return nil
	}
	select {
	case t.outQ <- payload:
	default:
		// 队列满丢最老.
		select {
		case <-t.outQ:
		default:
		}
		select {
		case t.outQ <- payload:
		default:
		}
	}
	return nil
}

// Req routine→kernel Req 查询.
func (t *GrpcClientTransport) Req(msg map[string]any) (map[string]any, error) {
	t.mu.Lock()
	stub := t.stub
	t.mu.Unlock()
	if stub == nil {
		return nil, fmt.Errorf("not connected")
	}
	ctx, cancel := context.WithTimeout(context.Background(), ClientReqTimeout)
	defer cancel()
	resp, err := stub.Req(ctx, mapToStruct(msg))
	if err != nil {
		return nil, err
	}
	return structAsMap(resp), nil
}

// GetRunningRoutines dial-in:经 Req unary 问 kernel.
func (t *GrpcClientTransport) GetRunningRoutines() ([]map[string]any, error) {
	resp, err := t.Req(map[string]any{"event": ReqGetRunningRoutines})
	if err != nil {
		return nil, err
	}
	routines, _ := resp["routines"].([]map[string]any)
	if routines == nil {
		// structAsMap 把 list 解析为 []any,需要二次转换.
		if list, ok := resp["routines"].([]any); ok {
			routines = make([]map[string]any, 0, len(list))
			for _, item := range list {
				if m, ok := item.(map[string]any); ok {
					routines = append(routines, m)
				}
			}
		}
	}
	return routines, nil
}

// GetRoutines dial-in:经 Req unary 问 kernel.
func (t *GrpcClientTransport) GetRoutines() ([]map[string]any, error) {
	resp, err := t.Req(map[string]any{"event": ReqGetRoutines})
	if err != nil {
		return nil, err
	}
	routines, _ := resp["routines"].([]map[string]any)
	if routines == nil {
		if list, ok := resp["routines"].([]any); ok {
			routines = make([]map[string]any, 0, len(list))
			for _, item := range list {
				if m, ok := item.(map[string]any); ok {
					routines = append(routines, m)
				}
			}
		}
	}
	return routines, nil
}

// GetModuleTree dial-in:经 Req unary 问 kernel 拉 module.tree.
func (t *GrpcClientTransport) GetModuleTree() (*ModuleTree, error) {
	if t.hub == nil {
		return nil, nil
	}
	resp, err := t.Req(map[string]any{"event": ReqGetModuleTree})
	if err != nil {
		return nil, err
	}
	t.hub.Query.CacheModuleTree(resp)
	return t.hub.Runtime.GetModuleTree(), nil
}

// Ready 返回 ready channel(连接成功时关闭).
func (t *GrpcClientTransport) Ready() <-chan struct{} {
	return t.ready
}

func (t *GrpcClientTransport) clearReady() {
	t.readyOnce.Do(func() {})
	// ready 是 once-close,不能 clear.重连期间用 ready 是否关闭判断"已就绪过",
	// 业务侧主要用阻塞 wait.
}

// --- 重连 loop ---

func (t *GrpcClientTransport) run() {
	defer close(t.runDone)
	backoff := BackoffInitial
	for {
		t.stopMu.Lock()
		stopped := t.stopFlag
		t.stopMu.Unlock()
		if stopped {
			return
		}

		connectedAt := time.Now()
		err := t.connectOnce()
		if err == nil {
			t.postConnect()
			err = t.recvLoop()
		}
		if err != nil {
			if t.hub != nil {
				t.hub.logger.Warnf("client: %v", err)
			}
		}
		t.clearStream()

		t.stopMu.Lock()
		stopped = t.stopFlag
		t.stopMu.Unlock()
		if stopped {
			return
		}

		// peer 断连:清 instance.
		if t.hub != nil {
			t.hub.OnPeerDown(t.peerID)
		}

		// 健康(连上后稳定)重置 backoff;连上即断保留 backoff.
		if time.Since(connectedAt).Seconds() >= ClientStableSeconds {
			backoff = BackoffInitial
		}
		if t.hub != nil {
			t.hub.logger.Infof("🔄 %v 后重连...", backoff)
		}
		time.Sleep(backoff)
		backoff = time.Duration(float64(backoff) * BackoffFactor)
		if backoff > BackoffMax {
			backoff = BackoffMax
		}
	}
}

func (t *GrpcClientTransport) connectOnce() error {
	ctx, cancel := context.WithTimeout(context.Background(), BackoffMax)
	defer cancel()
	conn, err := grpc.DialContext(ctx, t.address,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithBlock(),
	)
	if err != nil {
		return fmt.Errorf("dial %s: %w", t.address, err)
	}
	t.mu.Lock()
	t.channel = conn
	t.stub = NewRoutineServiceClient(conn)
	t.mu.Unlock()

	streamCtx, streamCancel := context.WithCancel(context.Background())
	stream, err := t.stub.Stream(streamCtx)
	if err != nil {
		streamCancel()
		return fmt.Errorf("open stream: %w", err)
	}

	t.streamMu.Lock()
	t.stream = stream
	t.streamMu.Unlock()

	// 关闭 stream 时 cancel ctx(Go 1.26 起 CloseSend 后还需 Recv 才能完整关闭).
	// 用一个 goroutine 监听 stop 信号 cancel.
	go func() {
		<-t.runDone
		streamCancel()
	}()

	t.readyOnce.Do(func() { close(t.ready) })
	if t.hub != nil {
		t.hub.logger.Infof("🔗 [client] connected to kernel: %s", t.address)
	}

	// 起 send loop.
	go t.sendLoop()

	return nil
}

func (t *GrpcClientTransport) postConnect() {
	if t.hub == nil {
		return
	}
	_, _ = t.GetModuleTree()
	_ = t.hub.SendCatalogPush(t.peerID)
}

func (t *GrpcClientTransport) recvLoop() error {
	t.streamMu.Lock()
	stream := t.stream
	t.streamMu.Unlock()
	if stream == nil {
		return fmt.Errorf("no stream")
	}
	for {
		msg, err := stream.Recv()
		if err != nil {
			return err
		}
		m := structAsMap(msg)
		if t.hub != nil {
			t.hub.DispatchInbound(t.peerID, m)
		}
	}
}

func (t *GrpcClientTransport) sendLoop() {
	t.streamMu.Lock()
	stream := t.stream
	t.streamMu.Unlock()
	if stream == nil {
		return
	}
	for {
		select {
		case payload := <-t.outQ:
			if err := stream.Send(mapToStruct(payload)); err != nil {
				if t.hub != nil {
					t.hub.logger.Warnf("client send error: %v", err)
				}
				return
			}
		case <-t.runDone:
			return
		}
	}
}

func (t *GrpcClientTransport) clearStream() {
	t.streamMu.Lock()
	if t.stream != nil {
		_ = t.stream.CloseSend()
	}
	t.stream = nil
	t.streamMu.Unlock()
	// 重置 outQ(避免旧消息堆积).
	t.mu.Lock()
	t.outQ = make(chan map[string]any, ClientOutQMaxSize)
	t.mu.Unlock()
}

// --- helpers ---

// RegisterRoutineServiceServer 注册 service 到 grpc server(对标 protoc 生成的 add_*_to_server).
func RegisterRoutineServiceServer(s *grpc.Server, srv RoutineServiceServer) {
	s.RegisterService(&routineServiceDesc, srv)
}

// peerIDFromContext 从 grpc stream ctx 取 peer id(对标 grpc context.peer()).
func peerIDFromContext(ctx context.Context) string {
	p, ok := peer.FromContext(ctx)
	if !ok {
		return "unknown"
	}
	return p.Addr.String()
}

// newUUID 简单的 req_id 生成(不用 uuid 库,timestamp + counter).
var uuidCounter uint64

func newUUID() string {
	// 简化:用时间戳 + 计数器,够用作为 req_id(无需 cryptographically unique).
	n := atomic.AddUint64(&uuidCounter, 1)
	return fmt.Sprintf("%x-%d", time.Now().UnixNano(), n)
}
