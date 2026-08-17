package shell

import (
	"context"
	"strconv"
	"time"

	"kernel/command"
	"kernel/conn"
	"kernel/module"
)

// parsePassive 解析 wire 单字段 ``is_passive``(恒定嵌套结构 ``{flag, kwargs}``).
// py 侧类声明 ``is_passive = true`` 序列化后即 ``{flag: true, kwargs: {}}``;
// dict 形态(routines.yaml kwargs 注入)= passive + auto-start 默认入参.
// 断言失败(flag 缺失/结构不对)零值返回(false, nil),不 panic.
func parsePassive(msg map[string]any) (bool, map[string]any) {
	ip, _ := msg["is_passive"].(map[string]any)
	flag, _ := ip["flag"].(bool)
	kwargs, _ := ip["kwargs"].(map[string]any)
	return flag, kwargs
}

// LoadCatalog 拉取 conn 的 routines/modules 并注册到路由表(dial-out:kernel→routine
// Req 拉).返回 passive routine 列表供常驻模式自动拉起.conn 没起或查询失败只 Warn
// 跳过----不阻塞(常驻模式靠重连,conn 后起来下次会拉到).
//
// 末尾广播模块视图给所有已连接 conn----RegisterRoutine 完成后 routines 字段才含本次
// 刚注册的 routine;return 前(即 AutoStartPassive 前)完成广播 → passive auto-start
// 时所有 conn 缓存已就绪.
//
// dial-in 不走此方法(方向矛盾不能 kernel→routine Req)----routine 连上后主动 push
// catalog,经 dispatchEvent → handleCatalogPush 处理.
//
// 单一真理源:完整模块视图只 kernel 知全貌(routineClients 跨 conn 累积),conn 不
// 自己查本地(跨 conn 查不到)----kernel 推,本地缓存.
func (m *Manager) LoadCatalog(c conn.Conn) []PassiveRoutine {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	// get_modules
	var modules []string
	if resp, err := c.Req(ctx, map[string]any{"event": conn.ReqGetModules}); err == nil {
		modules = conn.ToStringSlice(resp["modules"])
	} else {
		m.log.Warnf("拉 modules 失败(conn 可能没起): %v", err)
		return nil
	}

	// get_routines:每个 routine 一条 "add routine <name>" log.is_passive=True 的收集
	// 返回(常驻模式自动拉起).同时注册 name → connID 路由(跨 conn submit 按 name 查表).
	// 响应必带 hub_id(进程级身份,如 "zero"/"one"),kernel 校验唯一性----重复或为空
	// 则拒绝连接,Close 这条 conn.
	var routines []any
	if resp, err := c.Req(ctx, map[string]any{"event": conn.ReqGetRoutines}); err == nil {
		routines, _ = resp["routines"].([]any)
		// dial-out hub_id 校验:必填 + 唯一,失败则 Close 这条 conn.
		hubID, _ := resp["hub_id"].(string)
		if !m.registerHubID(c.ID(), hubID) {
			m.log.Errorf("⛔ 拒绝连接: hub_id=%q 无效或重复,关闭 conn %s(dial-out)", hubID, c.ID())
			_ = c.Close()
			return nil
		}
	}

	result := m.applyCatalog(c.ID(), modules, routines)
	// 推完整模块视图给所有已连接 conn(exclude="" 不排除).
	m.pushModuleView("")
	return result.Passives
}

// handleCatalogPush 处理 dial-in routine 主动 push 的 catalog(routines + modules):
// 注册路由表 + 收集 passive + 推 module.tree 给所有 conn + 起 passive.对标 dial-out
// 的 LoadCatalog(pull→register→pushView→AutoStart),只是 catalog 来源是 routine push
// 而非 kernel Req 拉----dial-in 方向矛盾不能 kernel→routine Req.
//
// 带 req_id 时处理完发 catalog.pushed 回执给来源 conn,带 {registered, skipped} 列表:
// py 据此打印结果(成功 N 条 / 跳过 M 条同名冲突).req_id 缺失时降级为 fire-and-forget
// (兼容不要求回执的调用方).
//
// payload: {event: catalog.push, req_id?, routines: [{name, is_passive: {flag, kwargs}, meta}, ...], modules: [...]}.
// 走入站 Stream → bus conn.event → dispatchEvent → 此方法.
func (m *Manager) handleCatalogPush(connID string, msg map[string]any) {
	// hub_id 校验(首次连接时):重复则拒绝连接,Close 这条 conn.
	// 不耦合业务:hub_id 仅用于身份标识 + 唯一性校验,通过后才继续 applyCatalog.
	hubID, _ := msg["hub_id"].(string)
	if !m.registerHubID(connID, hubID) {
		m.log.Errorf("⛔ 拒绝连接: hub_id=%q 重复,关闭 conn %s", hubID, connID)
		if c := m.Conn(connID); c != nil {
			_ = c.Close()
		}
		return
	}
	modules := conn.ToStringSlice(msg["modules"])
	routines, _ := msg["routines"].([]any)
	result := m.applyCatalog(connID, modules, routines)
	m.pushModuleView("")
	// 回执 catalog.pushed 带 registered/skipped 列表(py 据此打印结果).
	// 先回执再 auto-start(同 handleCatalogRegister 的顺序不变量).
	if reqID, _ := msg["req_id"].(string); reqID != "" {
		m.sendConn(connID, map[string]any{
			"event":      conn.CatalogPushed,
			"req_id":     reqID,
			"registered": result.Registered,
			"skipped":    result.Skipped,
		})
	}
	if len(result.Passives) > 0 {
		go m.AutoStartPassive(connID, result.Passives)
	}
}

// handleCatalogRegister 处理 catalog.register 单条增量注册(routine 运行时 register_routine).
// 对称 catalog.deregister / catalog.reload.带 req_id + 回执 catalog.registered:kernel 是
// 唯一真理源,py 等 ok=true 才本地 register;ok=false(同名冲突,不区分 conn)py 不本地 register.
//
// **同名一律 fail**(不区分 conn----无论同 conn 还是跨 conn,name 已存在就拒绝).
// 覆盖语义走 catalog.reload(handleCatalogReload → ReloadRoutine 不区分 conn 覆盖).
//
// is_passive=true 时注册成功后异步 auto-start(跟 conn up 时 LoadCatalog→AutoStartPassive
// 一致),让运行时注册的 passive routine 立即拉起而非等下次重连.
// 不重推 module.tree(catalog 变更跟模块拓扑无关).
//
// payload: {event: catalog.register, req_id, name, is_passive: {flag, kwargs}, meta}.
// 回执: {event: catalog.registered, req_id, ok, error?}.
func (m *Manager) handleCatalogRegister(connID string, msg map[string]any) {
	name, _ := msg["name"].(string)
	reqID, _ := msg["req_id"].(string)
	if name == "" {
		if reqID != "" {
			m.sendConn(connID, map[string]any{
				"event": conn.CatalogRegistered, "req_id": reqID,
				"ok": false, "error": "name is required",
			})
		}
		return
	}
	isPassive, passiveKwargs := parsePassive(msg)
	meta, _ := msg["meta"].(map[string]any)
	ok := m.RegisterRoutine(name, connID, isPassive, passiveKwargs, meta)
	reply := map[string]any{"event": conn.CatalogRegistered, "req_id": reqID, "ok": ok}
	if !ok {
		// 同名冲突(不区分 conn):告知 py 已被哪个 conn 占住(dispatchLoop 单线程,
		// RegisterRoutine 返 false 时路由未变,routeOf 拿到的就是当前持有者).
		if rt := m.routeOf(name); rt.clientID != "" {
			reply["error"] = "routine " + name + " already registered by conn " + rt.clientID
		} else {
			reply["error"] = "register rejected"
		}
		m.log.Warnf("⚠️ catalog.register %s rejected (name already exists)", name)
	} else {
		m.log.Infof("➕ catalog.register %s", name)
	}
	// 回执必须先于 auto-start 的 lifecycle.created 发出:py 等回执才本地 register,
	// created 抢在回执前到达会让 py 报 "routine not found".bus 出站 FIFO,
	// publish 顺序 = wire 顺序,先 sendConn 再起 goroutine 即保证.
	if reqID != "" {
		m.sendConn(connID, reply)
	}
	if ok && isPassive {
		// passive routine 注册成功后异步 auto-start(跟 conn up 时一致).
		// 异步避免阻塞 dispatch loop:Execute 等 created 回执要经 dispatch loop 投递.
		go m.AutoStartPassive(connID, []PassiveRoutine{{Name: name, Kwargs: passiveKwargs}})
	}
}

// handleCatalogReload 处理 catalog.reload 单条重载(routine 运行时 reload_routine).
// **不区分 conn,同名覆盖**(无论原归属是哪个 conn,新 reload 请求都覆盖路由).
// 对称 handleCatalogRegister:register 同名 fail,reload 同名覆盖.
//
// 总回执 ok=true(reload 不冲突,覆盖语义).用 req_id + catalog.reloaded 回执保持
// 跟 register/deregister 对称的 ack 流程(py 等 ok=true 才本地 Routines.register 覆盖).
//
// is_passive=true 时 reload 后异步 auto-start(ReloadRoutine 已停老实例 + 清
// passiveStarted[key],auto-start 能重新拉起新类实例).
//
// payload: {event: catalog.reload, req_id, name, is_passive: {flag, kwargs}, meta}.
// 回执: {event: catalog.reloaded, req_id, ok, error?}.
func (m *Manager) handleCatalogReload(connID string, msg map[string]any) {
	name, _ := msg["name"].(string)
	reqID, _ := msg["req_id"].(string)
	if name == "" {
		if reqID != "" {
			m.sendConn(connID, map[string]any{
				"event": conn.CatalogReloaded, "req_id": reqID,
				"ok": false, "error": "name is required",
			})
		}
		return
	}
	isPassive, passiveKwargs := parsePassive(msg)
	meta, _ := msg["meta"].(map[string]any)
	m.ReloadRoutine(name, connID, isPassive, passiveKwargs, meta)
	m.log.Infof("🔄 catalog.reload %s", name)
	// 回执先于 auto-start 的 created(同 handleCatalogRegister:py 等回执才本地
	// register 覆盖,publish 顺序 = wire 顺序).
	if reqID != "" {
		m.sendConn(connID, map[string]any{
			"event": conn.CatalogReloaded, "req_id": reqID, "ok": true,
		})
	}
	// passive routine reload 后异步 auto-start 新实例(ReloadRoutine 已停老实例),
	// 带新 passive_kwargs----yaml kwargs 变更经 reload 路径热生效.
	if isPassive {
		go m.AutoStartPassive(connID, []PassiveRoutine{{Name: name, Kwargs: passiveKwargs}})
	}
}

// handleCatalogDeregister 处理 catalog.deregister 单条移除(routine 运行时
// deregister_routine).两跳流程:kernel 不直接删路由,而是先发 catalog.deregister.cmd
// 给持有者 hub → hub 本地 dereg → 回执 catalog.deregister.cmd.ack → kernel 删路由
// + 回执请求者 catalog.deregistered.支持跨 hub dereg(请求者 ≠ 持有者).
//
// 流程:
// 1. name 不在路由表 → 回执请求者 ok=false(name 不存在)
// 2. name 在路由表 → 发 catalog.deregister.cmd{req_id, name} 给持有者 conn,
//    记 pendingDeregisters[req_id] = {requesterConnID, holderConnID, name}
// 3. (后续)handleCatalogDeregisterCmdAck 收到持有者回执 → 删路由 + 回执请求者
//
// 请求者 == 持有者时也走此流程(cmd 发回请求者自己),保持流程统一.
//
// payload: {event: catalog.deregister, req_id, name}.
// 回执: {event: catalog.deregistered, req_id, ok, error?}(异步,等 cmd.ack 后才发).
func (m *Manager) handleCatalogDeregister(connID string, msg map[string]any) {
	name, _ := msg["name"].(string)
	reqID, _ := msg["req_id"].(string)
	if name == "" {
		if reqID != "" {
			m.sendConn(connID, map[string]any{
				"event": conn.CatalogDeregistered, "req_id": reqID,
				"ok": false, "error": "name is required",
			})
		}
		return
	}
	// 查路由,找持有者
	rt := m.routeOf(name)
	if rt.clientID == "" {
		// name 不在路由表 → 回执请求者 ok=false
		if reqID != "" {
			m.sendConn(connID, map[string]any{
				"event": conn.CatalogDeregistered, "req_id": reqID,
				"ok": false, "error": "routine " + name + " not registered",
			})
		}
		m.log.Warnf("⚠️ catalog.deregister %s rejected (not registered)", name)
		return
	}
	holderConnID := rt.clientID
	// 记 pending,等 cmd.ack 回执
	m.mu.Lock()
	m.pendingDeregisters[reqID] = pendingDeregister{
		requesterConnID: connID,
		holderConnID:    holderConnID,
		name:            name,
	}
	m.mu.Unlock()
	// 发 cmd 给持有者(请求者 == 持有者时发回请求者自己)
	m.sendConn(holderConnID, map[string]any{
		"event": conn.CatalogDeregisterCmd, "req_id": reqID, "name": name,
	})
	m.log.Infof("📤 catalog.deregister %s → cmd to conn %s", name, holderConnID)
}

// handleCatalogDeregisterCmdAck 处理持有者 hub 的 catalog.deregister.cmd.ack 回执.
// kernel 收到后:删自身路由(校验 holderConnID 一致)→ 回执请求者 catalog.deregistered.
// 持有者 ok=false(本地 dereg 失败,罕见)→ kernel 不删路由,回执请求者 ok=false.
//
// payload: {event: catalog.deregister.cmd.ack, req_id, ok, error?}.
// 回执: {event: catalog.deregistered, req_id, ok, error?}(发给请求者).
func (m *Manager) handleCatalogDeregisterCmdAck(connID string, msg map[string]any) {
	reqID, _ := msg["req_id"].(string)
	ok, _ := msg["ok"].(bool)
	if reqID == "" {
		return
	}
	// 查 pending
	m.mu.Lock()
	pending, exists := m.pendingDeregisters[reqID]
	if exists {
		delete(m.pendingDeregisters, reqID)
	}
	m.mu.Unlock()
	if !exists {
		// 超时 / 重复 ack / 请求者已断开 → 丢弃
		m.log.Warnf("⚠️ catalog.deregister.cmd.ack req_id=%s no pending", reqID)
		return
	}
	// 校验 ack 来自持有者(防伪造)
	if connID != pending.holderConnID {
		m.log.Warnf("⚠️ catalog.deregister.cmd.ack req_id=%s from conn %s, expected holder %s",
			reqID, connID, pending.holderConnID)
		return
	}
	reply := map[string]any{"event": conn.CatalogDeregistered, "req_id": reqID, "ok": ok}
	if ok {
		// 持有者已本地 dereg → kernel 删路由(校验 holderConnID 一致,防竞态)
		m.DeregisterRoutine(pending.name, pending.holderConnID)
		m.log.Infof("➖ catalog.deregister %s (holder=%s)", pending.name, pending.holderConnID)
	} else {
		errMsg, _ := msg["error"].(string)
		if errMsg == "" {
			errMsg = "holder deregister failed"
		}
		reply["error"] = errMsg
		m.log.Warnf("⚠️ catalog.deregister %s holder rejected: %s", pending.name, errMsg)
	}
	// 回执请求者(请求者 == 持有者时发回持有者自己)
	m.sendConn(pending.requesterConnID, reply)
}

// applyCatalog 注册 routines 路由 + 收集 passive(catalog 拉取/push 共用).
// modules 只 log(实例级 modules 由 created 回报回带,catalog 不存).
//
// 仅 add 不 delete----运行时 unregister 走 catalog.deregister 单条删,断线走
// UnloadRemote 清本 conn 全部路由.无需 diff(断线已清干净,重连首帧 push 无 stale).
//
// 同名冲突(不区分 conn)→ RegisterRoutine 返 false,log warn 跳过该条(不进 passives,
// 路由不覆盖).冲突 name 收集进 result.Skipped,由 handleCatalogPush 经 catalog.pushed
// 回执告知 py(py 据此打印跳过列表).覆盖语义走 catalog.reload(单条,显式),重连首帧
// push 不走 reload(避免无意覆盖别 conn).
//
// CatalogApplyResult 是 applyCatalog 的返回值:passive 列表 + 成功/跳过 name 列表.
// handleCatalogPush 用 Registered/Skipped 组 catalog.pushed 回执给 py 打印.
type CatalogApplyResult struct {
	Passives   []PassiveRoutine
	Registered []string // 成功注册的 name
	Skipped    []string // 跳过的 name(同名冲突,先到先得)
}

func (m *Manager) applyCatalog(connID string, modules []string, routines []any) CatalogApplyResult {
	if len(modules) > 0 {
		m.log.Infof("📦 modules: %v", modules)
	}
	result := CatalogApplyResult{}
	for _, r := range routines {
		rm, ok := r.(map[string]any)
		if !ok {
			continue
		}
		name, _ := rm["name"].(string)
		if name == "" {
			continue
		}
		isPassive, passiveKwargs := parsePassive(rm)
		meta, _ := rm["meta"].(map[string]any)
		if m.RegisterRoutine(name, connID, isPassive, passiveKwargs, meta) {
			m.log.Infof("➕ [ROUTINE] %s", name)
			result.Registered = append(result.Registered, name)
			if isPassive {
				result.Passives = append(result.Passives,
					PassiveRoutine{Name: name, Kwargs: passiveKwargs})
			}
		} else {
			// 同名冲突:跳过(路由保持先到先得,不覆盖)
			result.Skipped = append(result.Skipped, name)
			if rt := m.routeOf(name); rt.clientID != "" {
				m.log.Warnf("⚠️ [ROUTINE] %s skipped (already owned by conn %s)", name, rt.clientID)
			}
		}
	}
	m.log.Infof("✅ registered modules=%d, routines=%d (skipped=%d)",
		len(modules), len(result.Registered), len(result.Skipped))
	return result
}

// pushModuleView 把完整模块视图(拓扑 + 全局 routine→modules catalog)广播给所有
// 已连接的 conn.catalog 变更时调:conn 连上注册完 routine(增长,LoadCatalog/
// handleCatalogPush 末尾)/ conn 断线卸载完 routine(缩小,lifeline down handler).
//
// excludeConnID 排除某 conn(断线广播时排除刚断的----已不可达).
//
// 方向分两路:dial-out 走同步 Req(保证 routine 缓存好并回执后 kernel 才继续,
// AutoStartPassive 前 module.tree 已就绪);dial-in 走 fire-and-forget Stream 事件
// (方向矛盾不能 Req,无 ack----routine 侧 conflict 在 tree 到达前调会抛,靠测试
// 选不调 conflict 的 routine 避开,production 后续补 ack 握手).
func (m *Manager) pushModuleView(excludeConnID string) {
	tree := module.Default()
	if tree == nil {
		m.log.Warnf("🌳 module tree 未初始化,跳过推送")
		return
	}
	conns := m.Conns()
	if len(conns) == 0 {
		return
	}
	payload := map[string]any{
		"event": conn.ModuleTree,
		"tree":  tree.Serialize(),
	}
	pushed := 0
	for _, c := range conns {
		if c.ID() == excludeConnID {
			continue
		}
		if c.DialIn() {
			// dial-in:fire-and-forget Stream 事件(不能 Req,方向矛盾).
			m.sendOut(c.ID(), payload)
			pushed++
			continue
		}
		// dial-out:同步 Req 保证 routine 侧缓存就绪.
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		resp, err := c.Req(ctx, payload)
		cancel()
		if err != nil {
			m.log.Warnf("🌳 推 module.tree 给 %s 失败: %v", c.ID(), err)
			continue
		}
		if ok, _ := resp["ok"].(bool); !ok {
			m.log.Warnf("🌳 %s 缓存 module.tree 失败: %v", c.ID(), resp)
			continue
		}
		pushed++
	}
	m.log.Infof("🌳 pushed module view to %d/%d conns", pushed, len(conns))
}

// RunningRoutines 返回当前所有 running 实例的 {name,id} 列表(cmd.State==StateRunning).
// 供 dial-in Req get_running_routines 查询:routine 据此按 name 找到对端 routine 的 id
// (跨进程正确:kernel 有全局 nodes 视图).created 态不列(未 started,还没 on_started).
func (m *Manager) RunningRoutines() []map[string]any {
	m.mu.Lock()
	defer m.mu.Unlock()
	routines := make([]map[string]any, 0, len(m.nodes))
	for _, n := range m.nodes {
		if n.cmd == nil || n.cmd.State() != command.StateRunning {
			continue
		}
		routines = append(routines, map[string]any{
			"name": n.cmd.Name,
			"id":   strconv.Itoa(n.cmd.ID),
		})
	}
	return routines
}

// HandleReq 处理 dial-in routine->kernel Req 查询(经 grpc Server.SetReqHandler 注入).
// 集中所有 Req 查询分发--grpc 包纯传输不持域知识(Req 只委托本 handler).
//   - get_module_tree:module.Default() 序列化(不依赖 shell nodes,但统一在此出口)
//   - get_running_routines:扫 nodes 回 [{name,id}]
//   - get_routines:全量路由表(catalog 注册的全部 routine,跨所有 conn)回 [{name,hub_id,is_passive,meta}]
func (m *Manager) HandleReq(msg map[string]any) (map[string]any, error) {
	event, _ := msg["event"].(string)
	switch event {
	case conn.ReqGetModuleTree:
		tree := module.Default()
		if tree == nil {
			return map[string]any{"ok": false, "error": "module tree not initialized"}, nil
		}
		return map[string]any{"ok": true, "tree": tree.Serialize()}, nil
	case conn.ReqGetRunningRoutines:
		return map[string]any{"routines": m.RunningRoutines()}, nil
	case conn.ReqGetRoutines:
		return map[string]any{"routines": m.ListRoutines()}, nil
	}
	return map[string]any{"error": "unknown event"}, nil
}
