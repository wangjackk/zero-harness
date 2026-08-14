package grpc_test

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"kernel/bus"
	"kernel/conn"
	kgrpc "kernel/grpc"
	"kernel/module"
	"kernel/shell"
)

// TestPythonDialInTransport 验证 Python GrpcClientTransport ↔ Go kernel dial-in server
// 双向 lifecycle 通(Phase ① 传输级):
//
//	Python client 拨 NewServer → 发 lifecycle.created → Go(onAccept echo)回 lifecycle.start
//	→ Python 收到后发 lifecycle.started → Go 收到.两端各证一方向.
//
// 需 demo/.venv 的 python(routine 已 editable install).缺则 t.Skip.
func TestPythonDialInTransport(t *testing.T) {
	py, fixture := findPythonAndFixture(t, "_dialin_client.py")

	// onAccept 在 Stream handler 里同步调(早于 readLoop 读首条消息),存 connID----
	// 全局 conn.event 订阅者据此过滤(happens-before:onAccept 存 connID → readLoop
	// publish created → 订阅者收,故 connID 一定已存).消除 per-accept 订阅竞速.
	var accepted atomic.Value // string

	srv, err := kgrpc.NewServer("127.0.0.1:0", func(c conn.Conn) {
		accepted.Store(c.ID())
	})
	if err != nil {
		t.Fatalf("NewServer: %v", err)
	}
	go func() { _ = srv.Start() }()
	defer srv.Close()

	started := make(chan struct{})
	var once sync.Once
	sub := bus.GetBus().Subscribe(conn.TopicEvent, 64, false)
	defer sub.Close()
	go func() {
		for payload := range sub.Recv() {
			ein, ok := payload.(conn.EventIn)
			if !ok {
				continue
			}
			acc, _ := accepted.Load().(string)
			if ein.ConnID != acc {
				continue
			}
			ev, _ := ein.Msg["event"].(string)
			switch ev {
			case "lifecycle.created":
				// echo start 回 Python(出站经 bus → ServerConn sendLoop → stream)
				bus.GetBus().Publish(conn.TopicOut+"."+acc, conn.OutMsg{Msg: map[string]any{
					"event": "lifecycle.start",
					"id":    ein.Msg["id"],
					"name":  ein.Msg["name"],
				}})
			case "lifecycle.started":
				once.Do(func() { close(started) })
			}
		}
	}()

	cmd := exec.Command(py, fixture, srv.Address())
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	if err := cmd.Start(); err != nil {
		t.Fatalf("start python: %v", err)
	}
	// cmd 退出前先等 started(Go 收到 lifecycle.started).
	select {
	case <-started:
	case <-time.After(20 * time.Second):
		_ = cmd.Process.Kill()
		t.Fatalf("timeout 等 lifecycle.started;python output:\n%s", out.String())
	}
	if err := cmd.Wait(); err != nil {
		t.Fatalf("python fixture 失败: %v;output:\n%s", err, out.String())
	}
}

// TestPythonDialInExecute 验证 routine 作为 client(拨入)的业务级全链路(Phase ②):
// spawn venv python 跑真 RoutineHub(start_client + Quick routine)→ routine 连上后
// 主动 push catalog(catalog.push)→ kernel 注册路由 → Execute quick →
// created→start→stopped 全过 Stream(不经 Req)→ 验证 stopped result.
//
// 区别于 TestPythonDialInTransport(传输级 echo 握手):这里走 RoutineHub 业务层
// (dispatch_inbound→handle_created/handle_start→routine.start()→stopped)+ 真
// Manager.Execute,真正测 kernel 服务器核心经 dial-in ServerConn.
func TestPythonDialInExecute(t *testing.T) {
	py, fixture := findPythonAndFixture(t, "_dialin_execute.py")

	// 初始化 module tree(shell.New 占根模块要用).tree.json 在 <root>/kernel/.
	cwd, _ := os.Getwd()
	root := filepath.Join(cwd, "..", "..")
	treeRoot, err := module.LoadFile(filepath.Join(root, "kernel", "tree.json"))
	if err != nil {
		t.Fatalf("load tree.json: %v", err)
	}
	module.Init(treeRoot)
	m := shell.New(module.Default())

	srv, err := kgrpc.NewServer("127.0.0.1:0", m.AddConn)
	if err != nil {
		t.Fatalf("NewServer: %v", err)
	}
	go func() { _ = srv.Start() }()
	defer srv.Close()

	// 拦截 lifecycle.stopped 拿 result 验证.
	stopped := make(chan map[string]any, 1)
	sub := bus.GetBus().Subscribe(conn.TopicEvent, 64, false)
	defer sub.Close()
	go func() {
		for payload := range sub.Recv() {
			ein, ok := payload.(conn.EventIn)
			if !ok {
				continue
			}
			if ev, _ := ein.Msg["event"].(string); ev == conn.LifecycleStopped {
				select {
				case stopped <- ein.Msg:
				default:
				}
			}
		}
	}()

	cmd := exec.Command(py, fixture, srv.Address())
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	if err := cmd.Start(); err != nil {
		t.Fatalf("start python: %v", err)
	}
	defer func() {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	}()

	// 等 catalog.push 注册 quick(routine 连上后主动 push).
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if m.HasRoutine("quick") {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if !m.HasRoutine("quick") {
		t.Fatalf("catalog.push 未注册 quick;python output:\n%s", out.String())
	}

	// Execute quick → created→start→stopped(全过 Stream,不经 Req).
	routine, err := m.Execute(m.RootID(), "quick", map[string]any{"msg": "hi"})
	if err != nil {
		t.Fatalf("Execute: %v;output:\n%s", err, out.String())
	}
	routine.Wait()

	select {
	case msg := <-stopped:
		result, _ := msg["result"].(map[string]any)
		if result["ok"] != true || result["echo"] != "hi" {
			t.Fatalf("stopped result 不符: %v;output:\n%s", result, out.String())
		}
	case <-time.After(10 * time.Second):
		t.Fatalf("timeout 等 stopped;output:\n%s", out.String())
	}
}

// findPythonAndFixture 定位 demo/.venv 的 python + dialin fixture.
// go test cwd = kernel/grpc,repo root = ../.. .缺则 t.Skip.
func findPythonAndFixture(t *testing.T, fixtureName string) (string, string) {
	t.Helper()
	cwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	root := filepath.Join(cwd, "..", "..")

	candidates := []string{
		filepath.Join(root, "demo", ".venv", "Scripts", "python.exe"), // Windows
		filepath.Join(root, "demo", ".venv", "bin", "python"),         // posix
	}
	var py string
	for _, c := range candidates {
		if _, err := os.Stat(c); err == nil {
			py = c
			break
		}
	}
	if py == "" {
		if p, err := exec.LookPath("python"); err == nil {
			py = p
		}
	}
	if py == "" {
		t.Skip("无 python(demo/.venv 与 PATH 均无)---- 跳过 dial-in 传输测试")
	}
	fixture := filepath.Join(root, "routine", "tests", fixtureName)
	if _, err := os.Stat(fixture); err != nil {
		t.Skipf("fixture 缺失: %s", fixture)
	}
	return py, fixture
}
