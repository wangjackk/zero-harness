package routine

import (
	"fmt"
	"time"
)

// LifecycleManager create / start / stop 三个生命周期入口.
// 对标 Python routine/lifecycle.py,砍掉 body consumer / shell cascade.
//
// _stoppedSent 幂等守卫保证一条 invocation 只发一次 stopped:runner(正常完成 / error)
// 与 stopRunner(stop / 超时)都调 sendStopped,先到者发,后到者 no-op.
type LifecycleManager struct {
	server  *RoutineHub
	runtime *ServerRuntime
}

// StopTimeout stop() hook 等 main task 退出的超时.
const StopTimeout = 3 * time.Second

func NewLifecycleManager(server *RoutineHub, runtime *ServerRuntime) *LifecycleManager {
	return &LifecycleManager{server: server, runtime: runtime}
}

// sendStopped 幂等发 lifecycle.stopped.先 markStoppedSent 守住,后到者 no-op.
func (m *LifecycleManager) sendStopped(prid, id, peerID, reason string,
	state *invocationState, result any, errMsg string) error {
	wireReason, ok := reasonToEnum[reason]
	if !ok {
		wireReason = ReasonUnknown
	}
	if state != nil && !state.markStoppedSent() {
		return nil
	}
	return m.server.SendLifecycleStopped(id, wireReason, result, errMsg, peerID)
}

// cleanup 清理 instance 路由表 + 注册表.对标 Python _cleanup.
func (m *LifecycleManager) cleanup(prid string) {
	if state := m.runtime.PopInstance(prid); state != nil {
		state.markNotStarted()
	}
	// rid 是 prid 末段(peerID 含 ':')
	rid := prid
	for i := len(prid) - 1; i >= 0; i-- {
		if prid[i] == ':' {
			rid = prid[i+1:]
			break
		}
	}
	m.runtime.PopCreated(rid)
	m.runtime.PopSubscriber(rid)
}

// instantiate created / start-fallback 共用的实例化.
// resolve instance → 注册路由表 → 绑 ctx → 存 init_kwargs → auto_subscribe.
func (m *LifecycleManager) instantiate(peerID, rid, name string,
	factory RoutineFactory, kwargs map[string]any) (Routine, *RunContext, error) {
	prid := peerID + ":" + rid
	instance, state := m.runtime.ResolveInstance(prid, factory)
	// restart / 复用时清终态(runner 守卫查它).fresh 实例 newInvocationState 已置 false.
	state.clearStopFinalized()
	// message.* 路由表在 created 注册:created 后即可收 req/stream/send.
	m.runtime.RegisterCreated(rid, state)
	// ctx 绑到 created:on_created() hook + 所有发送(req/publish/send/submit/...)可用.
	ctx := &RunContext{
		ID:      rid,
		Name:    name,
		PeerID:  peerID,
		io:      m.server,
		runtime: m.runtime,
		transport: m.server.Transport,
		state:   state,
		logger:  m.runtime.logger.Named(fmt.Sprintf("[ROUTINE] %s", name)),
	}
	state.ctxOnce.Do(func() { state.ctx = ctx })
	state.initKwargs = kwargs
	// pubsub 订阅在 created:同步等--created 回报前确保 kernel 订阅表已更新.
	m.autoSubscribe(instance, state, ctx)
	return instance, ctx, nil
}

// autoSubscribe 注册 @subscribe handler 表到 runtime + 发 pubsub.subscribe 给 kernel.
// 对标 Python instance._auto_subscribe().
func (m *LifecycleManager) autoSubscribe(instance Routine, state *invocationState, ctx *RunContext) {
	registrar, ok := instance.(HandlerRegistrar)
	if !ok {
		return
	}
	// HandlerRegistrar 在 OnCreated 中调 ctx.HandleSubscribe 注册,这里不再触发.
	// 此处仅占位,真正的 auto_subscribe 是 OnCreated 调 ctx.HandleSubscribe 完成.
	_ = registrar
}

// HandleCreated lifecycle.created(调度器→server):实例化 + 绑 ctx + 注册路由表 +
// 建 inbox + auto_subscribe,然后发 lifecycle.created 回报.
func (m *LifecycleManager) HandleCreated(peerID string, msg map[string]any) {
	rid, _ := msg["id"].(string)
	name, _ := msg["name"].(string)
	kwargs, _ := msg["kwargs"].(map[string]any)
	if kwargs == nil {
		kwargs = map[string]any{}
	}

	factory := m.runtime.Registry.Get(name)
	if factory == nil {
		m.runtime.logger.Errorf("routine not found: %s#%s", name, rid)
		_ = m.sendStopped(peerID+":"+rid, rid, peerID, "error", nil, nil, "")
		return
	}

	instance, ctx, err := m.instantiate(peerID, rid, name, factory, kwargs)
	if err != nil {
		m.runtime.logger.Errorf("%s#%s instantiate failed: %v", name, rid, err)
		_ = m.sendStopped(peerID+":"+rid, rid, peerID, "error", nil, nil, err.Error())
		return
	}

	// 注册 handler(若实现 HandlerRegistrar)
	if registrar, ok := instance.(HandlerRegistrar); ok {
		registrar.RegisterHandlers(ctx)
	}

	// instance.OnCreated() 用户钩子(早于 start):返回声明的 modules.
	var mods []string
	if hooks, ok := instance.(LifecycleHooks); ok {
		result, err := hooks.OnCreated(rid, kwargs)
		if err != nil {
			m.runtime.logger.Errorf("%s#%s OnCreated failed: %v", name, rid, err)
		} else if len(result) > 0 {
			mods = make([]string, 0, len(result))
			for _, mo := range result {
				mods = append(mods, fmt.Sprint(mo))
			}
		}
	}
	_ = m.server.SendLifecycleCreated(rid, mods, peerID)
}

// HandleStart lifecycle.start:跑 routine 的 Run 体.
func (m *LifecycleManager) HandleStart(peerID string, msg map[string]any) {
	rid, _ := msg["id"].(string)
	name, _ := msg["name"].(string)
	prid := peerID + ":" + rid

	factory := m.runtime.Registry.Get(name)
	if factory == nil {
		_ = m.sendStopped(prid, rid, peerID, "error", nil, nil, "")
		return
	}

	state := m.runtime.GetInstance(prid)
	var instance Routine
	var ctx *RunContext
	if state == nil {
		// created 还没到(理论不该发生):兜底走 instantiate,kwargs 用 {}.
		var err error
		instance, ctx, err = m.instantiate(peerID, rid, name, factory, map[string]any{})
		if err != nil {
			_ = m.sendStopped(prid, rid, peerID, "error", nil, nil, err.Error())
			return
		}
		state = m.runtime.GetInstance(prid)
	} else {
		instance = state.instance
		ctx = state.ctx
	}

	startKwargs := state.initKwargs
	if startKwargs == nil {
		startKwargs = map[string]any{}
	}

	m.runtime.Go(func() {
		m.runRunner(prid, rid, peerID, name, instance, ctx, state, startKwargs)
	})
}

// runRunner runner 主体:ack_start → on_started → run() → on_stopped → send_stopped → cleanup.
func (m *LifecycleManager) runRunner(prid, rid, peerID, name string,
	instance Routine, ctx *RunContext, state *invocationState, kwargs map[string]any) {
	if state.isStopFinalized() {
		return
	}
	state.resetForStart()

	// 通信能力在 created 已全部就绪.run 只负责跑 Run() 体.
	var result any
	var runErr error

	// ack_start:发 lifecycle.started 通知调度器.
	if err := ctx.AckStart(); err != nil {
		m.runtime.logger.Errorf("%s#%s ack_start failed: %v", name, rid, err)
	}

	state.markStarted() // 父已 started,可 start/stop 子 routine
	if hooks, ok := instance.(LifecycleHooks); ok {
		if err := hooks.OnStarted(); err != nil {
			m.runtime.logger.Errorf("%s#%s OnStarted failed: %v", name, rid, err)
		}
	}

	result, runErr = instance.Run(ctx, kwargs)

	// routine yield 自动收尾(对齐 Python async generator 自然结束):
	// Run 中调过 ctx.Yield → 框架发 is_final=true;Run 返回 error 则带 error.
	// yield 模式下 result 不走 lifecycle.stopped(Python yield 不返回值).
	if state.isYieldUsed() {
		if runErr != nil {
			_ = m.server.SendYield(rid, nil, true, runErr.Error(), peerID)
		} else {
			_ = m.server.SendYield(rid, nil, true, "", peerID)
		}
		result = nil
	}

	if runErr != nil {
		m.runtime.logger.Errorf("%s#%s run failed: %v", name, rid, runErr)
		if hooks, ok := instance.(LifecycleHooks); ok {
			_ = hooks.OnStopped("error", nil, runErr.Error())
		}
		_ = m.sendStopped(prid, rid, peerID, "error", state, nil, runErr.Error())
		m.cleanup(prid)
		return
	}

	reason := "auto"
	if state.isStopRequested() {
		reason = "stop"
	}
	if hooks, ok := instance.(LifecycleHooks); ok {
		_ = hooks.OnStopped(reason, result, "")
	}
	_ = m.sendStopped(prid, rid, peerID, reason, state, result, "")
	m.cleanup(prid)
}

// HandleDestroy lifecycle.destroy:调度器销毁 created 态 routine(已 submit 未 start).
func (m *LifecycleManager) HandleDestroy(peerID string, msg map[string]any) {
	rid, _ := msg["id"].(string)
	prid := peerID + ":" + rid
	state := m.runtime.GetInstance(prid)
	if state == nil {
		return
	}
	if hooks, ok := state.instance.(LifecycleHooks); ok {
		_ = hooks.OnStopped("stop", nil, "")
	}
	_ = m.sendStopped(prid, rid, peerID, "stop", state, nil, "")
	m.cleanup(prid)
}

// HandleStop lifecycle.stop:打断 started 态的 runner.
func (m *LifecycleManager) HandleStop(peerID string, msg map[string]any) {
	rid, _ := msg["id"].(string)
	prid := peerID + ":" + rid
	state := m.runtime.GetInstance(prid)
	if state == nil {
		return
	}
	if !state.beginStop() {
		return
	}
	stopReason, _ := msg["reason"].(string)
	stopBy, _ := msg["by"].(string)
	force := stopReason == "force"

	m.runtime.Go(func() {
		defer state.clearStopInProgress()
		if !state.isStopRequested() {
			state.requestStop()
		}
		var stopResult any
		if hooks, ok := state.instance.(LifecycleHooks); ok {
			r, err := hooks.Stop()
			if err == nil {
				stopResult = r
			} else {
				m.runtime.logger.Errorf("%s#%s Stop() failed: %v", state.instance.Name(), rid, err)
			}
		}
		state.finalizeStop()
		state.markNotStarted()
		// 等 main task done,超时放弃(此处只 best-effort,Go 无 channel 拦截 main task 的能力)
		select {
		case <-state.mainDone:
		case <-time.After(StopTimeout):
			m.runtime.logger.Warnf("%s#%s main task did not exit; abandon", state.instance.Name(), rid)
		}
		onReason := "stop"
		onDetail := ""
		if force {
			onReason = "force"
			if stopBy != "" {
				onDetail = "evicted by " + stopBy
			}
		}
		if hooks, ok := state.instance.(LifecycleHooks); ok {
			_ = hooks.OnStopped(onReason, stopResult, onDetail)
		}
		_ = m.sendStopped(prid, rid, peerID, onReason, state, stopResult, "")
		m.cleanup(prid)
	})
}

// ForceStopPeer peer 断连时强制清理该 peer 的所有 running instance.
func (m *LifecycleManager) ForceStopPeer(peerID string) {
	prids := m.runtime.InstancesByPeer(peerID)
	for _, prid := range prids {
		m.forceStopOne(peerID, prid)
	}
}

func (m *LifecycleManager) forceStopOne(peerID, prid string) {
	state := m.runtime.GetInstance(prid)
	if state == nil {
		return
	}
	if !state.beginStop() {
		return
	}
	state.finalizeStop()
	state.markNotStarted()
	rid := prid
	for i := len(prid) - 1; i >= 0; i-- {
		if prid[i] == ':' {
			rid = prid[i+1:]
			break
		}
	}
	m.runtime.logger.Infof("⏹️ force stop %s#%s (peer %s disconnected)", state.instance.Name(), rid, peerID)
	defer state.clearStopInProgress()
	if hooks, ok := state.instance.(LifecycleHooks); ok {
		_ = hooks.OnStopped("disconnect", nil, "")
	}
	_ = m.sendStopped(prid, rid, peerID, "disconnect", state, nil, "")
	m.cleanup(prid)
}