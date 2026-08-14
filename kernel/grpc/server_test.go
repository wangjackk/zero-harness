package grpc

import (
	"context"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/types/known/structpb"

	"kernel/bus"
	"kernel/conn"
)

// TestServerConnTransport 验证 dial-in conn 的双向传输事实(conn 不再有 future/lifecycle
// 方法----那些在 shell.Manager + node 上):
//   - 出站:publish OutMsg 到 conn.out.<id> → sendLoop 调 stream.Send → 对端 Recv 收到.
//   - 入站:对端 stream.Send → readLoop → publish conn.event(EventIn).
//   - Req:dial-in 返回 ErrDialInNoReq.
//   - conn 生命周期:accept→conn.up,断开→conn.down.
func TestServerConnTransport(t *testing.T) {
	// 1. 起 kernel grpc server(dial-in).onAccept 经 channel 把 ServerConn 传给测试.
	acceptedCh := make(chan conn.Conn, 1)
	srv, err := NewServer("127.0.0.1:0", func(c conn.Conn) { acceptedCh <- c })
	if err != nil {
		t.Fatalf("NewServer: %v", err)
	}
	go func() { _ = srv.Start() }()
	defer srv.Close()

	// 2. 订阅 bus:conn.event(入站事件)+ conn.conn(生命周期).
	eventSub := bus.GetBus().Subscribe(conn.TopicEvent, 256, true)
	defer eventSub.Close()
	connSub := bus.GetBus().Subscribe(conn.TopicConn, 16, false)
	defer connSub.Close()

	// 3. test client dial kernel server(模拟 routine 进程).
	cliConn, err := grpc.NewClient(srv.Address(),
		grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer cliConn.Close()
	cli := NewRoutineServiceClient(cliConn)
	stream, err := cli.Stream(context.Background())
	if err != nil {
		t.Fatalf("Stream: %v", err)
	}

	// 4. 等 ServerConn 被 accept + conn.up.
	sc, ok := waitAccepted(acceptedCh, 2*time.Second)
	if !ok {
		t.Fatalf("ServerConn not accepted in time")
	}
	if cc := recvConnChange(connSub, 2*time.Second); cc == nil || cc.Kind != "up" || !cc.IsDialIn {
		t.Fatalf("expected conn.up dial-in, got %v", cc)
	}
	// sendLoop 在 Stream handler 里 go 起,给它一点时间订阅 conn.out topic
	// (publish 早于 sub 建立会被 bus 丢弃).
	time.Sleep(50 * time.Millisecond)

	// 5. Req 返回 ErrDialInNoReq(dial-in 不支持 kernel→routine Req).
	if _, err := sc.Req(context.Background(), map[string]any{"event": "x"}); err != conn.ErrDialInNoReq {
		t.Fatalf("Req err = %v, want conn.ErrDialInNoReq", err)
	}

	// 6. 出站:publish OutMsg 到 conn.out.<sc.ID()> → sendLoop 发 → test client Recv.
	outMsg := map[string]any{"event": "test.out", "id": "1", "data": "hello"}
	bus.GetBus().Publish(conn.TopicOut+"."+sc.ID(), conn.OutMsg{Msg: outMsg})
	m, err := stream.Recv()
	if err != nil {
		t.Fatalf("outbound Recv: %v", err)
	}
	got := m.AsMap()
	if got["event"] != "test.out" || got["id"] != "1" {
		t.Fatalf("outbound msg = %v, want event=test.out id=1", got)
	}

	// 7. 入站:test client Send → readLoop → publish conn.event(EventIn).
	if err := send(stream, map[string]any{"event": "test.in", "id": "2"}); err != nil {
		t.Fatalf("inbound Send: %v", err)
	}
	timer := time.After(2 * time.Second)
	for {
		select {
		case p := <-eventSub.Recv():
			ev, ok := p.(conn.EventIn)
			if !ok || ev.ConnID != sc.ID() {
				continue
			}
			if conn.Event(ev.Msg) != "test.in" {
				t.Fatalf("inbound event = %v, want test.in", conn.Event(ev.Msg))
			}
			goto inboundOK
		case <-timer:
			t.Fatalf("inbound event not received on bus in time")
		}
	}
inboundOK:

	// 8. 断开 test client → readLoop Recv err → conn.down.
	_ = stream.CloseSend()
	if cc := recvConnChange(connSub, 2*time.Second); cc == nil || cc.Kind != "down" {
		t.Fatalf("expected conn.down, got %v", cc)
	}
}

// TestServerConnDisconnect 验证 dial-in 断线被动感知:test client 断开 → readLoop Recv err
// → publish conn.down.
func TestServerConnDisconnect(t *testing.T) {
	acceptedCh := make(chan conn.Conn, 1)
	srv, _ := NewServer("127.0.0.1:0", func(c conn.Conn) { acceptedCh <- c })
	go func() { _ = srv.Start() }()
	defer srv.Close()

	connSub := bus.GetBus().Subscribe(conn.TopicConn, 16, false)
	defer connSub.Close()

	cliConn, _ := grpc.NewClient(srv.Address(),
		grpc.WithTransportCredentials(insecure.NewCredentials()))
	defer cliConn.Close()
	cli := NewRoutineServiceClient(cliConn)
	stream, _ := cli.Stream(context.Background())

	sc, ok := waitAccepted(acceptedCh, 2*time.Second)
	if !ok {
		t.Fatalf("not accepted")
	}
	if cc := recvConnChange(connSub, 2*time.Second); cc == nil || cc.Kind != "up" {
		t.Fatalf("expected conn.up, got %v", cc)
	}
	_ = sc.ID() // 触发使用(保留 sc 引用,避免 lint 抱怨未用)

	// 断开 test client → readLoop Recv err → publish conn.down.
	_ = stream.CloseSend()
	if cc := recvConnChange(connSub, 2*time.Second); cc == nil || cc.Kind != "down" {
		t.Fatalf("expected conn.down, got %v", cc)
	}
}

// send 把 map 发到 stream(test client 出站).
func send(stream RoutineService_StreamClient, msg map[string]any) error {
	s, err := structpb.NewStruct(msg)
	if err != nil {
		return err
	}
	return stream.Send(s)
}

// waitAccepted 从 channel 取 conn(onAccept 在 Stream accept 时异步发).
func waitAccepted(ch <-chan conn.Conn, timeout time.Duration) (*ServerConn, bool) {
	select {
	case c := <-ch:
		return c.(*ServerConn), true
	case <-time.After(timeout):
		return nil, false
	}
}

// recvConnChange 从 sub 非阻塞取一条 ConnChange(带超时).
func recvConnChange(sub *bus.Subscriber, timeout time.Duration) *conn.ConnChange {
	select {
	case p := <-sub.Recv():
		cc, _ := p.(conn.ConnChange)
		return &cc
	case <-time.After(timeout):
		return nil
	}
}
