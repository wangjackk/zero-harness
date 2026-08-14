package shell_test

import (
	"context"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/types/known/structpb"

	"kernel/command"
	"kernel/conn"
	kgrpc "kernel/grpc"
	"kernel/module"
	"kernel/shell"
)

// TestExecuteRoundTrip 端到端验证全走 bus 的 wiring:Manager.Execute → sendCreated
// (publish conn.out.<id>)→ ServerConn sendLoop → stream → test client 收到 created →
// 回 created return → readLoop → publish conn.event → dispatchEvent resolveCreated →
// node.createdCh → Execute 继续 → sendStart → started → ... → stopped.
//
// 不依赖 python:test client 模拟 routine server 回 lifecycle.created/started/stopped.
// 验证 1a+step2 的总线对称模型:出站经 bus,入站经 bus,future 在 node,Manager 解析.
func TestExecuteRoundTrip(t *testing.T) {
	tree := module.NewTree("root", map[string]module.ModuleRecord{
		"root":   {Children: []string{"figure"}},
		"figure": {Children: []string{"body"}},
		"body":   {},
	})
	m := shell.New(tree)

	// 起 kernel grpc server(dial-in).onAccept 注册 conn + 注册 routine 路由.
	acceptedCh := make(chan conn.Conn, 1)
	srv, err := kgrpc.NewServer("127.0.0.1:0", func(c conn.Conn) {
		m.AddConn(c)
		m.RegisterRoutine("test_routine", c.ID(), false, nil, nil)
		acceptedCh <- c
	})
	if err != nil {
		t.Fatalf("NewServer: %v", err)
	}
	go func() { _ = srv.Start() }()
	defer srv.Close()

	// test client dial kernel server(模拟 routine 进程).
	cliConn, err := grpc.NewClient(srv.Address(),
		grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer cliConn.Close()
	cli := kgrpc.NewRoutineServiceClient(cliConn)
	stream, err := cli.Stream(context.Background())
	if err != nil {
		t.Fatalf("Stream: %v", err)
	}

	// test client goroutine:读 kernel 发来的 lifecycle 命令,回对应回报.
	// created 回报带 modules(模拟 created() 返回值).
	go func() {
		for {
			m, err := stream.Recv()
			if err != nil {
				return
			}
			msg := m.AsMap()
			id, _ := msg["id"].(string)
			switch conn.Event(msg) {
			case conn.LifecycleCreated:
				sendStream(stream, map[string]any{
					"event": conn.LifecycleCreated, "id": id, "modules": []any{"body"},
				})
			case conn.LifecycleStart:
				sendStream(stream, map[string]any{"event": conn.LifecycleStarted, "id": id})
			case conn.LifecycleStop:
				sendStream(stream, map[string]any{"event": conn.LifecycleStopped, "id": id})
			}
		}
	}()

	// 等 conn accept.
	select {
	case <-acceptedCh:
	case <-time.After(2 * time.Second):
		t.Fatal("conn not accepted")
	}

	// Execute = Create + sendCreated + 等 created + Start(→ runRemote sendStart + 等 started).
	cmd, err := m.Execute(m.RootID(), "test_routine", nil)
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}

	// 等 started(runRemote 收到 started → MarkRunning).短暂轮询 cmd.State.
	deadline := time.After(3 * time.Second)
	for cmd.State() != command.StateRunning {
		select {
		case <-deadline:
			t.Fatalf("routine not running in time, state=%v", cmd.State())
		case <-time.After(10 * time.Millisecond):
		}
	}

	// 停掉(级联停自己)→ runRemote ctx.Done → sendStop → 等 stopped → cmd done.
	m.Stop(cmd.ID)
	done := make(chan struct{})
	go func() {
		cmd.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("routine did not stop in time")
	}

	// 再 Execute 一条同 name 应能成功(新 node,新 chan,无残留等待者).
	cmd2, err := m.Execute(m.RootID(), "test_routine", nil)
	if err != nil {
		t.Fatalf("second Execute: %v", err)
	}
	m.Stop(cmd2.ID)
	cmd2.Wait()
}

func sendStream(stream kgrpc.RoutineService_StreamClient, msg map[string]any) {
	s, _ := structpb.NewStruct(msg)
	_ = stream.Send(s)
}
