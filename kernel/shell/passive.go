package shell

import (
	"strings"
)

// PassiveRoutine 是一条 server 端 routine 的目录信息(get_routines 拉来的).
// Kwargs 是 auto-start 默认入参(routines.yaml 条目 kwargs 透传),Execute 带参
// 拉起,py 侧 run(kwargs) 自然收到----配置随注册一次流动,无二次读取.
type PassiveRoutine struct {
	Name   string
	Kwargs map[string]any
}

// AutoStartPassive 自动拉起某 client 名下的 passive routine.
//
// server 端 is_passive=True 的 routine 在 client 注册时由 scheduler 自动 fabricate
// 一次 lifecycle.start 拉起(用 routine 自己声明的 modules).passiveStarted 按
// (clientID, name) 去重----同一 server 重连后重复调不会重复 start;不同 server 同名
// passive 各自独立起一个(按 client 隔离,不互相挤掉).
//
// passive 挂 root 下(parentID=m.RootID()).clientID 用于 passiveStarted 去重 +
// RegisterRoutine 已在 catalog 拉取时把 name→clientID 注册进路由表(Execute 内部
// Create 按 name 查表会查到本 clientID,一致).
func (m *Manager) AutoStartPassive(clientID string, passives []PassiveRoutine) {
	rootID := m.RootID()
	for _, p := range passives {
		key := clientID + "\x00" + p.Name
		m.mu.Lock()
		if m.passiveStarted[key] {
			m.mu.Unlock()
			continue
		}
		m.passiveStarted[key] = true
		m.mu.Unlock()
		// Execute = Create + Start.Create 按 name 查路由表定 clientID(=本 client).
		// modules 由 created 回报回带(catalog 不再缓存 modules),Execute 内部等
		// created 回报填 declared 再 Start TryAcquire.kwargs = 注册时透传的
		// passive_kwargs(routines.yaml 条目),作为 run() 入参传下去.
		if _, err := m.Execute(rootID, p.Name, p.Kwargs); err != nil {
			// 启动失败(模块冲突等):回退标志让下次重连可重试,并打日志.
			m.mu.Lock()
			delete(m.passiveStarted, key)
			m.mu.Unlock()
			m.log.Errorf("passive %s auto-start failed (client %s): %v",
				p.Name, clientID, err)
			continue
		}
		m.log.Infof("auto-start passive routine: %s (client %s)",
			p.Name, clientID)
	}
}

// StopRunningByName 停止名为 name 的运行中 routine 实例,走正统 Stop 流程.
//
// 用于 reload_routine / deregister_routine 的热替换:覆盖/删除路由表前先停老实例,
// 否则还在运行的老实例会泄漏,新代码不会被加载.passive 只保证注册后自动拉起,
// 是否常驻(run() 返回即退 or park 等事件)是业务选择----统一停所有运行中实例,
// 不区分 passive/普通.
//
// clientID 语义:
//   - ""        : 停所有 conn 名下名为 name 的实例(reload 用----"不区分 conn 覆盖",
//                 老 conn 的实例成孤儿必须全停).
//   - 非 ""     : 只停该 conn 名下的实例(deregister 用----deregister 只删本 conn 路由).
//
// 走 m.Stop(id) 而非直接 cmd.Stop():Stop 会递归级联停整棵子树("父必须等子")+
// 发 lifecycle.stop 让 py 侧 stop() hook 做应用层清理(如 warp_screen 的 _kill_proc_tree),
// 避免 reload 有子 routine 在跑时只停父泄漏子.
//
// 异步起 goroutine 调 Stop:reload/deregister handler 在 dispatch loop 里同步调本函数,
// 而 Stop 等 lifecycle.stopped 回执要经同一 dispatch loop 投递,同步会死锁(对标
// handler.go 里 evict/force_acquire 都起独立 goroutine 跑 stop 的模式).
// 清 passiveStarted[key] 在锁内同步做(让重连能重起,不依赖 Stop 完成时机).
func (m *Manager) StopRunningByName(name, clientID string) {
	m.mu.Lock()
	var ids []int
	for _, n := range m.nodes {
		if n.cmd.Name == name && (clientID == "" || n.clientID == clientID) {
			ids = append(ids, n.cmd.ID)
		}
	}
	// 清 passiveStarted:reload(clientID="")清所有该 name 的 key;deregister 清单 conn 的 key.
	if clientID == "" {
		suffix := "\x00" + name
		for k := range m.passiveStarted {
			if strings.HasSuffix(k, suffix) {
				delete(m.passiveStarted, k)
			}
		}
	} else {
		delete(m.passiveStarted, clientID+"\x00"+name)
	}
	m.mu.Unlock()

	if len(ids) == 0 {
		return
	}
	go func() {
		for _, id := range ids {
			m.Stop(id) // 幂等:已 stopping/不存在则 no-op.内部递归级联停子树.
		}
		scope := clientID
		if scope == "" {
			scope = "all conns"
		}
		m.log.Infof("stopped %d running instance(s) of %s (client=%s) for hot-reload/dereg",
			len(ids), name, scope)
	}()
}

// UnloadRemote 卸载指定 client 名下的所有远端 routine 节点(stream 断线时由 client
// OnDisconnect 回调触发,带 clientID).root 保留(本地骨架).其它 client 的 routine
// 不受影响----精准卸载,对标老版 router.removeClientData(clientID).
//
// 远端 routine 的模块已由 runRemote defer Release 释放,这里补一刀防御性 Release +
// stop command(idempotent),并清该 client 的 passiveStarted + routineClients 路由表
// 项让重连后能重起 / 重新注册.同步调用(在 reader goroutine 退出前完成):保证卸载
// 干净后才会触发重连 reload.
func (m *Manager) UnloadRemote(clientID string) {
	m.mu.Lock()
	rootID := m.root.ID
	var dead []*node
	for id, n := range m.nodes {
		if id == rootID {
			continue
		}
		if n.clientID == clientID {
			dead = append(dead, n)
		}
	}
	// 摘死节点 + 从父链移除(父可能是 root 或同 client 的父 routine).
	for _, n := range dead {
		delete(m.nodes, n.cmd.ID)
		if n.parent != nil {
			removeChild(n.parent, n)
		}
	}
	// 清该 client 的 passive 标志 + routineClients 路由表项:重连后重新注册/拉起.
	prefix := clientID + "\x00"
	for k := range m.passiveStarted {
		if strings.HasPrefix(k, prefix) {
			delete(m.passiveStarted, k)
		}
	}
	for name, r := range m.routineClients {
		if r.clientID == clientID {
			delete(m.routineClients, name)
		}
	}
	// 清该 conn 的 hub_id 映射:断线后该 hub_id 释放,重连时重新校验唯一性.
	delete(m.hubIDs, clientID)
	// 清该 conn 的 entry:断线后旧 conn 对象不再可用,重连时 AddConn 用新 ID 注册.
	// 不删则 m.conns 只增不减, pushModuleView 遍历到死 conn 超时/丢弃, len 虚高.
	delete(m.conns, clientID)
	m.mu.Unlock()

	for _, n := range dead {
		n.cmd.Stop()             // idempotent:已 stopped 则 no-op
		m.tree.Release(n.cmd.ID) // 防御性:runRemote defer 已释放,重复 Release 安全
	}
	m.log.Warnf("unloaded %d remote routines (client %s disconnected)",
		len(dead), clientID)
}
