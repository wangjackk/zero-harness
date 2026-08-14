package shell

import (
	"context"
	"strconv"
	"testing"
	"time"

	"kernel/bus"
	"kernel/conn"
	"kernel/module"
)

// fakeConn ?conn.Conn 实现(?ID/Req/Close/WaitReady/DialIn).
type fakeConn struct {
	id string
}

// outCapture 订阅 bus ?conn.out.<connID> topic,捕获出站消息供测试断言回执.
type outCapture struct {
	t   *testing.T
	sub *bus.Subscriber
}

func captureOut(t *testing.T, m *Manager, connID string) *outCapture {
	c := &outCapture{
		t:   t,
		sub: bus.GetBus().Subscribe(conn.TopicOut+"."+connID, 64, false),
	}
	return c
}

func (c *outCapture) reset() {
	for {
		select {
		case <-c.sub.Recv():
		default:
			return
		}
	}
}

// waitAck ?req_id ?catalog.registered/deregistered 回执.
func waitAck(c *outCapture, reqID string) map[string]any {
	timeout := time.After(time.Second)
	for {
		select {
		case v := <-c.sub.Recv():
			om, ok := v.(conn.OutMsg)
			if !ok {
				continue
			}
			msg := om.Msg
			if id, _ := msg["req_id"].(string); id == reqID {
				return msg
			}
		case <-timeout:
			c.t.Fatalf("waitAck timeout: req_id=%s not received", reqID)
			return nil
		}
	}
}

// waitMsg ?event + req_id 都匹配的消息(两跳流程区分 cmd / deregistered).
func waitMsg(c *outCapture, event, reqID string) map[string]any {
	timeout := time.After(time.Second)
	for {
		select {
		case v := <-c.sub.Recv():
			om, ok := v.(conn.OutMsg)
			if !ok {
				continue
			}
			msg := om.Msg
			if ev, _ := msg["event"].(string); ev == event {
				if id, _ := msg["req_id"].(string); id == reqID {
					return msg
				}
			}
		case <-timeout:
			c.t.Fatalf("waitMsg timeout: event=%s req_id=%s not received", event, reqID)
			return nil
		}
	}
}

func (f *fakeConn) ID() string                                                  { return f.id }
func (f *fakeConn) Req(context.Context, map[string]any) (map[string]any, error) { return nil, nil }
func (f *fakeConn) Close() error                                                { return nil }
func (f *fakeConn) WaitReady() bool                                             { return true }
func (f *fakeConn) DialIn() bool                                                { return false }

func newTestManager() *Manager {
	tree := module.NewTree("root", map[string]module.ModuleRecord{
		"root":   {Children: []string{"figure", "core"}},
		"figure": {Children: []string{"head", "body"}},
		"head":   {},
		"body":   {Children: []string{"leg"}},
		"leg":    {},
		"core":   {Children: []string{"mouth"}},
		"mouth":  {},
	})
	return New(tree)
}

// TestFutureResolve 验证 dispatchEvent ?future chan 解析:created/started/stopped
// ?bus ?Manager resolve node chan.以及 outfail + failPending ?
func TestFutureResolve(t *testing.T) {
	m := newTestManager()
	c := &fakeConn{id: "1"}
	m.AddConn(c)
	m.RegisterRoutine("test_routine", "1", false, nil, nil)

	cmd, err := m.Create(m.RootID(), "test_routine", nil)
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	id := strconv.Itoa(cmd.ID)
	n := m.nodeByID(id)
	if n == nil {
		t.Fatalf("node not found")
	}

	// created 回报 ?createdCh ?modules + createdDone=true.
	bus.GetBus().Publish(conn.TopicEvent, conn.EventIn{ConnID: "1", Msg: map[string]any{
		"event": conn.LifecycleCreated, "id": id, "modules": []any{"body"},
	}})
	select {
	case r := <-n.createdCh:
		if r.Err != nil || len(r.Modules) != 1 || r.Modules[0] != "body" {
			t.Fatalf("createdCh = %+v, want modules=[body]", r)
		}
	case <-time.After(time.Second):
		t.Fatal("createdCh not resolved")
	}
	m.mu.Lock()
	cd := n.createdDone
	m.mu.Unlock()
	if !cd {
		t.Fatal("createdDone not set after created")
	}

	// started 回报 ?startedCh.
	bus.GetBus().Publish(conn.TopicEvent, conn.EventIn{ConnID: "1", Msg: map[string]any{
		"event": conn.LifecycleStarted, "id": id,
	}})
	select {
	case <-n.startedCh:
	case <-time.After(time.Second):
		t.Fatal("startedCh not resolved")
	}

	// stopped 回报 ?stoppedCh(createdDone ?true,?stopped 分支,不走 created 失败).
	bus.GetBus().Publish(conn.TopicEvent, conn.EventIn{ConnID: "1", Msg: map[string]any{
		"event": conn.LifecycleStopped, "id": id, "reason": conn.ReasonStop,
	}})
	select {
	case <-n.stoppedCh:
	case <-time.After(time.Second):
		t.Fatal("stoppedCh not resolved")
	}
}

// TestCreatedFailure 验证 created 失败:created 未到先到 stopped ?resolve createdCh with err
// (不走 stoppedCh,?.
func TestCreatedFailure(t *testing.T) {
	m := newTestManager()
	c := &fakeConn{id: "2"}
	m.AddConn(c)
	m.RegisterRoutine("test_routine", "2", false, nil, nil)

	cmd, err := m.Create(m.RootID(), "test_routine", nil)
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	id := strconv.Itoa(cmd.ID)
	n := m.nodeByID(id)

	// stopped ?created ??created 失败.
	bus.GetBus().Publish(conn.TopicEvent, conn.EventIn{ConnID: "2", Msg: map[string]any{
		"event": conn.LifecycleStopped, "id": id, "reason": conn.ReasonError,
	}})
	select {
	case r := <-n.createdCh:
		if r.Err == nil {
			t.Fatal("expected createdCh err on created-failure, got nil")
		}
	case <-time.After(time.Second):
		t.Fatal("createdCh not resolved on created-failure")
	}
	// stoppedCh ?resolve(created ?createdCh).
	select {
	case <-n.stoppedCh:
		t.Fatal("stoppedCh should not be resolved on created-failure")
	default:
	}
}

// TestOutFail 验证出站 send 失败回流:OutFail(created) ?resolve createdCh with err;
// OutFail(start/stop) ?resolve stoppedCh.
func TestOutFail(t *testing.T) {
	m := newTestManager()
	c := &fakeConn{id: "3"}
	m.AddConn(c)
	m.RegisterRoutine("test_routine", "3", false, nil, nil)

	cmd, err := m.Create(m.RootID(), "test_routine", nil)
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	id := strconv.Itoa(cmd.ID)
	n := m.nodeByID(id)

	// outfail(created) ?createdCh err.
	bus.GetBus().Publish(conn.TopicOutFail, conn.OutFail{ConnID: "3", ID: id, Event: conn.LifecycleCreated})
	select {
	case r := <-n.createdCh:
		if r.Err == nil {
			t.Fatal("expected createdCh err on outfail, got nil")
		}
	case <-time.After(time.Second):
		t.Fatal("createdCh not resolved on outfail")
	}

	// outfail(stop) ?stoppedCh.
	bus.GetBus().Publish(conn.TopicOutFail, conn.OutFail{ConnID: "3", ID: id, Event: conn.LifecycleStop})
	select {
	case <-n.stoppedCh:
	case <-time.After(time.Second):
		t.Fatal("stoppedCh not resolved on outfail(stop)")
	}
}

// TestFailPending 验证 conn.down ?failPending:resolve ?conn 名下 routine ?// createdCh + stoppedCh with err.
func TestFailPending(t *testing.T) {
	m := newTestManager()
	c := &fakeConn{id: "4"}
	m.AddConn(c)
	m.RegisterRoutine("test_routine", "4", false, nil, nil)

	cmd, err := m.Create(m.RootID(), "test_routine", nil)
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	id := strconv.Itoa(cmd.ID)
	n := m.nodeByID(id)

	m.failPending("4")
	select {
	case r := <-n.createdCh:
		if r.Err == nil {
			t.Fatal("expected createdCh err on failPending, got nil")
		}
	case <-time.After(time.Second):
		t.Fatal("createdCh not resolved on failPending")
	}
	select {
	case <-n.stoppedCh:
	case <-time.After(time.Second):
		t.Fatal("stoppedCh not resolved on failPending")
	}
}

// TestHandleCatalogRegister 验证 catalog.register 单条增量注册:
// 1. 正常注册 name ?connID 路由
// 2. ?name 静默跳过(?req_id ?ok=false)
// 3. ?conn 同名 ?fail(?fail,?conn;?catalog.reload)
// 4. ?conn 同名 ??ok=false, 路由不变)
// 5. ?req_id ?catalog.registered{req_id, ok, error?}
func TestHandleCatalogRegister(t *testing.T) {
	m := newTestManager()
	c1 := &fakeConn{id: "1"}
	m.AddConn(c1)

	// 正常注册单条(?req_id,?ok=true)
	out1 := captureOut(t, m, "1")
	m.handleCatalogRegister("1", map[string]any{
		"req_id": "r1", "name": "agent_a/list_skills",
		"is_passive": map[string]any{"flag": true, "kwargs": map[string]any{}},
	})
	if !m.HasRoutine("agent_a/list_skills") {
		t.Fatal("catalog.register ?agent_a/list_skills 应已注册")
	}
	if rt := m.routeOf("agent_a/list_skills"); rt.clientID != "1" {
		t.Errorf("route clientID = %q, want 1", rt.clientID)
	}
	// 回执 catalog.registered{req_id=r1, ok=true}
	ack1 := waitAck(out1, "r1")
	if ack1["ok"] != true {
		t.Errorf("ack1 ok = %v, want true (msg: %v)", ack1["ok"], ack1)
	}

	// ?name 跳过(?req_id ?ok=false)
	out1.reset()
	m.handleCatalogRegister("1", map[string]any{"req_id": "r2"})
	ack2 := waitAck(out1, "r2")
	if ack2["ok"] != false {
		t.Errorf("?name ?ok=false, got: %v", ack2)
	}

	// ?conn 同名 ?fail(?fail,?conn;?catalog.reload)
	out1.reset()
	m.handleCatalogRegister("1", map[string]any{
		"req_id": "r3", "name": "agent_a/list_skills",
	})
	ack3 := waitAck(out1, "r3")
	if ack3["ok"] != false {
		t.Errorf("?conn ?ok=false (?fail), got: %v", ack3)
	}
	if ack3["error"] == nil || ack3["error"] == "" {
		t.Errorf("?conn 同名拒绝应带 error, got: %v", ack3)
	}
	// 路由不变(?c1)
	if rt := m.routeOf("agent_a/list_skills"); rt.clientID != "1" {
		t.Errorf("?conn 同名 fail ?clientID = %q, want 1 (路由不变)", rt.clientID)
	}

	// ?conn 同名 ??ok=false, 路由不变)
	c2 := &fakeConn{id: "2"}
	m.AddConn(c2)
	out2 := captureOut(t, m, "2")
	m.handleCatalogRegister("2", map[string]any{
		"req_id": "r4", "name": "agent_a/list_skills",
	})
	ack4 := waitAck(out2, "r4")
	if ack4["ok"] != false {
		t.Errorf("?conn ?ok=false, got: %v", ack4)
	}
	if ack4["error"] == nil || ack4["error"] == "" {
		t.Errorf("?conn 拒绝应带 error, got: %v", ack4)
	}
	// ?c1(不被 c2 覆盖)
	if rt := m.routeOf("agent_a/list_skills"); rt.clientID != "1" {
		t.Errorf("?conn ?clientID = %q, want 1 (先到先得)", rt.clientID)
	}
}

// TestHandleCatalogReload 验证 catalog.reload 单条重载(?conn 覆盖):
// 1. 正常 reload(首次)?ok=true,路由归属 reload ?conn
// 2. ?conn 同名 reload ?ok=true(覆盖)
// 3. ?conn 同名 reload ?ok=true(覆盖,路由转新 conn)
// 4. ?name 静默跳过(?req_id ?ok=false)
// 5. ?req_id ?catalog.reloaded{req_id, ok, error?}
func TestHandleCatalogReload(t *testing.T) {
	m := newTestManager()
	c1 := &fakeConn{id: "1"}
	c2 := &fakeConn{id: "2"}
	m.AddConn(c1)
	m.AddConn(c2)

	// 正常 reload(首次,等价 register)?ok=true,?c1
	out1 := captureOut(t, m, "1")
	m.handleCatalogReload("1", map[string]any{
		"req_id": "rl1", "name": "agent_a/list_skills",
	})
	if !m.HasRoutine("agent_a/list_skills") {
		t.Fatal("catalog.reload ?agent_a/list_skills 应已注册")
	}
	if rt := m.routeOf("agent_a/list_skills"); rt.clientID != "1" {
		t.Errorf("route clientID = %q, want 1", rt.clientID)
	}
	ack1 := waitAck(out1, "rl1")
	if ack1["ok"] != true {
		t.Errorf("ack1 ok = %v, want true (msg: %v)", ack1["ok"], ack1)
	}

	// ?conn 同名 reload ?ok=true(覆盖)
	out1.reset()
	m.handleCatalogReload("1", map[string]any{
		"req_id": "rl2", "name": "agent_a/list_skills",
	})
	ack2 := waitAck(out1, "rl2")
	if ack2["ok"] != true {
		t.Errorf("?conn 同名 reload ?ok=true, got: %v", ack2)
	}
	if rt := m.routeOf("agent_a/list_skills"); rt.clientID != "1" {
		t.Errorf("?conn reload ?clientID = %q, want 1", rt.clientID)
	}

	// ?conn 同名 reload ?ok=true(覆盖,?c2)
	out2 := captureOut(t, m, "2")
	m.handleCatalogReload("2", map[string]any{
		"req_id": "rl3", "name": "agent_a/list_skills",
	})
	ack3 := waitAck(out2, "rl3")
	if ack3["ok"] != true {
		t.Errorf("?conn 同名 reload ?ok=true (覆盖), got: %v", ack3)
	}
	if rt := m.routeOf("agent_a/list_skills"); rt.clientID != "2" {
		t.Errorf("?conn reload ?clientID = %q, want 2 (覆盖)", rt.clientID)
	}

	// ?name 跳过(?req_id ?ok=false)
	out2.reset()
	m.handleCatalogReload("2", map[string]any{"req_id": "rl4"})
	ack4 := waitAck(out2, "rl4")
	if ack4["ok"] != false {
		t.Errorf("?name reload ?ok=false, got: %v", ack4)
	}
}

// TestHandleCatalogDeregister 验证 catalog.deregister 两跳流程:
// 1. ?== ?kernel ?cmd ???ack ?kernel ?+ 回执
// 2. ?hub dereg:???kernel ?cmd ???ack ?kernel ?+ ?// 3. ?ack ok=false ?kernel 不删路由,?ok=false
// 4. name ??kernel 不发 cmd,?ok=false
// 5. ?name ?直接回执 ok=false
func TestHandleCatalogDeregister(t *testing.T) {
	m := newTestManager()
	c1 := &fakeConn{id: "1"}
	c2 := &fakeConn{id: "2"}
	m.AddConn(c1)
	m.AddConn(c2)

	// 初始: c1 注册 a/b, c2 注册 c
	m.handleCatalogRegister("1", map[string]any{"name": "a"})
	m.handleCatalogRegister("1", map[string]any{"name": "b"})
	m.handleCatalogRegister("2", map[string]any{"name": "c"})

	// 1. c1 deregister b (?== ?:
	// kernel ?cmd ?c1 ??handleCatalogDeregisterCmdAck ?kernel ?+ 回执 c1
	out1 := captureOut(t, m, "1")
	m.handleCatalogDeregister("1", map[string]any{"req_id": "d1", "name": "b"})
	// ??ack)
	if !m.HasRoutine("b") {
		t.Error("b 应在 ack 后才删")
	}
	// c1 收到 cmd
	cmd1 := waitMsg(out1, conn.CatalogDeregisterCmd, "d1")
	if cmd1["name"] != "b" {
		t.Errorf("cmd name = %v, want b", cmd1["name"])
	}
	// ?handleCatalogDeregisterCmdAck(同步,模拟 c1 本地 dereg ?kernel)
	m.handleCatalogDeregisterCmdAck("1", map[string]any{
		"event": conn.CatalogDeregisterCmdAck, "req_id": "d1", "ok": true,
	})
	// 路由已删
	if m.HasRoutine("b") {
		t.Error("b 应在 ack 后删除")
	}
	// c1 收到 deregistered 回执
	ack1 := waitMsg(out1, conn.CatalogDeregistered, "d1")
	if ack1["ok"] != true {
		t.Errorf("c1 deregister b ?ok=true, got: %v", ack1)
	}
	if !m.HasRoutine("a") || !m.HasRoutine("c") {
		t.Error("a 和 c 不应受影响")
	}

	// 2. ?hub dereg:c2 deregister a (a 归属 c1):
	// kernel ?cmd ?c1 ?c1 ack ?kernel ?+ 回执 c2
	out2 := captureOut(t, m, "2")
	m.handleCatalogDeregister("2", map[string]any{"req_id": "d2", "name": "a"})
	// c1 收到 cmd(?
	cmd2 := waitMsg(out1, conn.CatalogDeregisterCmd, "d2")
	if cmd2["name"] != "a" {
		t.Errorf("cmd name = %v, want a", cmd2["name"])
	}
	// 路由未删(?ack 之前)
	if !m.HasRoutine("a") {
		t.Error("a 应在 ack 后才删")
	}
	// c1 ack(?同步)
	m.handleCatalogDeregisterCmdAck("1", map[string]any{
		"event": conn.CatalogDeregisterCmdAck, "req_id": "d2", "ok": true,
	})
	// 路由已删
	if m.HasRoutine("a") {
		t.Error("a 应在 ack 后删除")
	}
	// c2 收到 deregistered 回执(?
	ack2 := waitMsg(out2, conn.CatalogDeregistered, "d2")
	if ack2["ok"] != true {
		t.Errorf("?hub dereg a ?ok=true, got: %v", ack2)
	}

	// 3. ?ack ok=false ?kernel 不删路由,?ok=false
	// 重新注册 a ?c1
	m.handleCatalogRegister("1", map[string]any{"name": "a"})
	// c2 deregister a,c1 ack ok=false
	out2.reset()
	m.handleCatalogDeregister("2", map[string]any{"req_id": "d3", "name": "a"})
	waitMsg(out1, conn.CatalogDeregisterCmd, "d3")
	m.handleCatalogDeregisterCmdAck("1", map[string]any{
		"event": conn.CatalogDeregisterCmdAck, "req_id": "d3",
		"ok": false, "error": "local dereg failed",
	})
	// 路由没删(ack ok=false)
	if !m.HasRoutine("a") {
		t.Error("holder ack ok=false 时不应删路由")
	}
	ack3 := waitMsg(out2, conn.CatalogDeregistered, "d3")
	if ack3["ok"] != false {
		t.Errorf("holder ack ok=false 时请求者应收到 ok=false, got: %v", ack3)
	}
	if ack3["error"] == nil || ack3["error"] == "" {
		t.Errorf("应带 error, got: %v", ack3)
	}

	// 4. name ??kernel 不发 cmd,?ok=false
	out1.reset()
	m.handleCatalogDeregister("1", map[string]any{"req_id": "d4", "name": "not_exists"})
	ack4 := waitMsg(out1, conn.CatalogDeregistered, "d4")
	if ack4["ok"] != false {
		t.Errorf("deregister ?name ?ok=false, got: %v", ack4)
	}

	// 5. ?name ?直接回执 ok=false
	out1.reset()
	m.handleCatalogDeregister("1", map[string]any{"req_id": "d5"})
	ack5 := waitMsg(out1, conn.CatalogDeregistered, "d5")
	if ack5["ok"] != false {
		t.Errorf("?name ?ok=false, got: %v", ack5)
	}

	// 最后剩 a(c1) + c(c2)
	if !m.HasRoutine("a") || !m.HasRoutine("c") {
		t.Errorf("?a ?c, ? %+v", m.routineClients)
	}
}

// TestDeregisterRoutineDirect 验证 DeregisterRoutine ?
// ?true, ?/ ?conn ?false.
func TestDeregisterRoutineDirect(t *testing.T) {
	m := newTestManager()
	c1 := &fakeConn{id: "1"}
	c2 := &fakeConn{id: "2"}
	m.AddConn(c1)
	m.AddConn(c2)

	m.RegisterRoutine("shared", "1", false, nil, nil)

	// c2 ?(?conn) ?false
	if m.DeregisterRoutine("shared", "2") {
		t.Error("?conn deregister ?false")
	}
	if !m.HasRoutine("shared") {
		t.Error("?conn deregister 不应删除")
	}

	// c1 ??true
	if !m.DeregisterRoutine("shared", "1") {
		t.Error("归属 conn deregister ?true")
	}
	if m.HasRoutine("shared") {
		t.Error("deregister 后不应再存在")
	}

	// 不存在的 name ?false
	if m.DeregisterRoutine("not_exists", "1") {
		t.Error("deregister 不存在的 name ?false")
	}
}

// TestApplyCatalogNoDelete 验证 applyCatalog 是纯 add, 不做 diff 删除:
// ?push 只剩部分 routine ? 之前注册的不被清 (运行时移除走 catalog.deregister).
func TestApplyCatalogNoDelete(t *testing.T) {
	m := newTestManager()
	c1 := &fakeConn{id: "1"}
	m.AddConn(c1)

	// 首次 push: a/b/c
	m.applyCatalog("1", nil, []any{
		map[string]any{"name": "a"},
		map[string]any{"name": "b"},
		map[string]any{"name": "c"},
	})

	// ?push 只剩 a (b/c 被运行时 unregister) ?applyCatalog ?b/c
	// (运行时移除已通过 catalog.deregister 单条同步; ?stale)
	m.applyCatalog("1", nil, []any{
		map[string]any{"name": "a"},
	})

	if !m.HasRoutine("a") {
		t.Error("a 应存在")
	}
	if !m.HasRoutine("b") {
		t.Error("b ?(applyCatalog 不做 diff 删除, ?catalog.deregister 负责)")
	}
	if !m.HasRoutine("c") {
		t.Error("c ?(applyCatalog 不做 diff 删除, ?catalog.deregister 负责)")
	}
}
