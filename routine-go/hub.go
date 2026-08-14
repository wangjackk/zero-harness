package routine

import (
	"errors"
	"fmt"
	"sync"
	"time"
)

// RoutineHub routine 生命周期 + 跨 routine 通信(精简版,传输无关).
// 对标 Python routine/server.py RoutineHub.
//
// 传输经 Transport 抽象(GrpcServerTransport dial-out / GrpcClientTransport dial-in)----
// 本结构不关心 wire:入站 transport → DispatchInbound 按 event 分发(lifecycle.start/stop/
// destroy/created → LifecycleManager;其余 → onInbound);出站 Send* helpers build payload
// 后调 transport.SendEvent.peer 断开 → OnPeerDown → lifecycle.ForceStopPeer.
type RoutineHub struct {
	Runtime   *ServerRuntime
	Lifecycle *LifecycleManager
	Query     *QueryService
	Transport Transport

	// HubID 进程级稳定身份(如 "zero"/"one"),随 catalog.push 发给 kernel.
	// kernel 校验唯一性,重复则拒绝连接.
	HubID string

	logger *Logger

	// onInbound 分发表:event → handler(msg).未知 event table-miss → no-op.
	inboundHandlers map[string]func(msg map[string]any)
	inboundMu       sync.RWMutex
}

// AckTimeout catalog.register/reload/deregister 等 kernel ack 默认超时.
const HubAckTimeout = 30 * time.Second

// NewRoutineHub 创建 hub.transport 可为 nil(测试场景);正常路径必须有.
func NewRoutineHub(registry *Registry, modules []string, transport Transport, hubID string) (*RoutineHub, error) {
	if hubID == "" {
		return nil, errors.New("hub_id is required (non-empty string, e.g. \"zero\"/\"one\")")
	}
	runtime := NewServerRuntime(registry, modules)
	hub := &RoutineHub{
		Runtime:   runtime,
		Transport: transport,
		HubID:     hubID,
		logger:    runtime.logger,
	}
	hub.Lifecycle = NewLifecycleManager(hub, runtime)
	hub.Query = NewQueryService(hub, runtime)
	runtime.PrintSummary()
	hub.buildInboundHandlers()
	return hub, nil
}

func (h *RoutineHub) buildInboundHandlers() {
	h.inboundHandlers = map[string]func(msg map[string]any){
		MessageDelivered:             h.onMessageDelivered,
		RoutineYielded:               h.onYielded,
		PubsubDelivered:              h.onPubsubDelivered,
		MessageReqDelivered:          h.onMessageReqDelivered,
		MessageReqReplyDelivered:     h.onMessageReqReplyDelivered,
		MessageStreamOpenDelivered:   h.onMessageStreamOpenDelivered,
		MessageStreamDataDelivered:   h.onMessageStreamDataDelivered,
		MessageStreamCancelDelivered: h.onMessageStreamCancelDelivered,
		LifecycleStarted:             h.onLifecycleStarted,
		LifecycleStopped:             h.onLifecycleStopped,
		RoutineRejected:              h.onRoutineRejected,
		RoutineSubmitted:             h.onRoutineSubmitted,
		RoutineAcquired:              h.onRoutineAcquired,
		RoutineModuleLoaded:          h.onRoutineModuleLoaded,
		RoutineModuleUnloaded:        h.onRoutineModuleUnloaded,
		RoutineReleased:              h.onRoutineReleased,
		CatalogRegistered:            h.onCatalogRegistered,
		CatalogReloaded:              h.onCatalogReloaded,
		CatalogDeregisterCmd:         h.onCatalogDeregisterCmd,
		CatalogDeregistered:          h.onCatalogDeregistered,
		CatalogPushed:                h.onCatalogPushed,
	}
}

// --- 出站:RoutineIO 实现 ---

func (h *RoutineHub) sendEvent(payload map[string]any, peerID string) error {
	if h.Transport == nil {
		return errors.New("no transport bound")
	}
	return h.Transport.SendEvent(payload, peerID)
}

// SendLifecycleCreated 发 lifecycle.created 回报给 kernel.
func (h *RoutineHub) SendLifecycleCreated(id string, modules []string, peerID string) error {
	payload := map[string]any{
		"event": LifecycleCreated,
		"id":    id,
	}
	if modules != nil {
		payload["modules"] = modules
	}
	return h.sendEvent(payload, peerID)
}

// SendLifecycleStarted 发 lifecycle.started.
func (h *RoutineHub) SendLifecycleStarted(id string, peerID string) error {
	return h.sendEvent(map[string]any{"event": LifecycleStarted, "id": id}, peerID)
}

// SendLifecycleStopped 发 lifecycle.stopped.
func (h *RoutineHub) SendLifecycleStopped(id string, reason string, result any, errMsg string, peerID string) error {
	payload := map[string]any{
		"event":  LifecycleStopped,
		"id":     id,
		"reason": reason,
	}
	if result != nil {
		payload["result"] = result
	}
	if errMsg != "" {
		payload["error"] = errMsg
	}
	return h.sendEvent(payload, peerID)
}

// RequestStop 请求停止一个 routine.
func (h *RoutineHub) RequestStop(id string, peerID string) error {
	if peerID == "" {
		return errors.New("RequestStop requires peer_id")
	}
	h.Lifecycle.HandleStop(peerID, map[string]any{"id": id})
	return nil
}

// SendRoutineSubmit 发 routine.submit 给 kernel.
func (h *RoutineHub) SendRoutineSubmit(reqID, parentID, name string, kwargs map[string]any, peerID string) error {
	return h.sendEvent(map[string]any{
		"event":     RoutineSubmit,
		"req_id":    reqID,
		"parent_id": parentID,
		"name":      name,
		"kwargs":    kwargs,
	}, peerID)
}

// SendRoutineStart 发 routine.start.
func (h *RoutineHub) SendRoutineStart(childID string, tryStart bool, peerID string) error {
	payload := map[string]any{"event": RoutineStart, "child_id": childID}
	if tryStart {
		payload["try"] = true
	}
	return h.sendEvent(payload, peerID)
}

// SendRoutineStop 发 routine.stop.
func (h *RoutineHub) SendRoutineStop(childID string, peerID string) error {
	return h.sendEvent(map[string]any{"event": RoutineStop, "child_id": childID}, peerID)
}

// SendRoutineUnsubmit 发 routine.unsubmit.
func (h *RoutineHub) SendRoutineUnsubmit(childID string, peerID string) error {
	return h.sendEvent(map[string]any{"event": RoutineUnsubmit, "child_id": childID}, peerID)
}

// SendRoutineForceStart 发 routine.force_start.
func (h *RoutineHub) SendRoutineForceStart(childID string, peerID string) error {
	return h.sendEvent(map[string]any{"event": RoutineForceStart, "child_id": childID}, peerID)
}

// SendRoutineAcquire 发 routine.acquire.
func (h *RoutineHub) SendRoutineAcquire(reqID, id string, modules []string, peerID string) error {
	return h.sendEvent(map[string]any{
		"event":   RoutineAcquire,
		"req_id":  reqID,
		"id":      id,
		"modules": modules,
	}, peerID)
}

// SendRoutineRelease 发 routine.release.
func (h *RoutineHub) SendRoutineRelease(reqID, id string, modules []string, peerID string) error {
	return h.sendEvent(map[string]any{
		"event":   RoutineRelease,
		"req_id":  reqID,
		"id":      id,
		"modules": modules,
	}, peerID)
}

// SendRoutineForceAcquire 发 routine.force_acquire.
func (h *RoutineHub) SendRoutineForceAcquire(reqID, id string, modules []string, peerID string) error {
	return h.sendEvent(map[string]any{
		"event":   RoutineForceAcquire,
		"req_id":  reqID,
		"id":      id,
		"modules": modules,
	}, peerID)
}

// SendRoutineForceRelease 发 routine.force_release.
func (h *RoutineHub) SendRoutineForceRelease(reqID, id string, modules []string, peerID string) error {
	return h.sendEvent(map[string]any{
		"event":   RoutineForceRelease,
		"req_id":  reqID,
		"id":      id,
		"modules": modules,
	}, peerID)
}

// SendRoutineLoadModule 发 routine.load_module.
func (h *RoutineHub) SendRoutineLoadModule(reqID, parentID, childID, name string, peerID string) error {
	return h.sendEvent(map[string]any{
		"event":     RoutineLoadModule,
		"req_id":    reqID,
		"parent_id": parentID,
		"child_id":  childID,
		"name":      name,
	}, peerID)
}

// SendRoutineUnloadModule 发 routine.unload_module.
func (h *RoutineHub) SendRoutineUnloadModule(reqID, childID string, peerID string) error {
	return h.sendEvent(map[string]any{
		"event":    RoutineUnloadModule,
		"req_id":   reqID,
		"child_id": childID,
	}, peerID)
}

// SendMessage 发 message.* 事件.
func (h *RoutineHub) SendMessage(targetIDs []string, sendEvent string, data map[string]any, sourceID, peerID string) error {
	return h.sendEvent(map[string]any{
		"event":      sendEvent,
		"target_ids": targetIDs,
		"data":       data,
		"source_id":  sourceID,
	}, peerID)
}

// SendPubsubSubscribe 发 pubsub.subscribe.
func (h *RoutineHub) SendPubsubSubscribe(id, topic, namespace, peerID string) error {
	return h.sendEvent(map[string]any{
		"event":     PubsubSubscribe,
		"id":        id,
		"topic":     topic,
		"namespace": namespace,
	}, peerID)
}

// SendPubsubUnsubscribe 发 pubsub.unsubscribe.
func (h *RoutineHub) SendPubsubUnsubscribe(id, topic, namespace, peerID string) error {
	return h.sendEvent(map[string]any{
		"event":     PubsubUnsubscribe,
		"id":        id,
		"topic":     topic,
		"namespace": namespace,
	}, peerID)
}

// SendPubsubPublish 发 pubsub.publish.
func (h *RoutineHub) SendPubsubPublish(topic string, data any, sourceID, namespace, peerID string) error {
	payload := map[string]any{
		"event":     PubsubPublish,
		"topic":     topic,
		"source_id": sourceID,
		"namespace": namespace,
	}
	if data != nil {
		payload["data"] = data
	}
	return h.sendEvent(payload, peerID)
}

// SendYield 发 routine.yield.
func (h *RoutineHub) SendYield(id string, data any, isFinal bool, errMsg string, peerID string) error {
	payload := map[string]any{
		"event":    RoutineYield,
		"id":       id,
		"is_final": isFinal,
	}
	if data != nil {
		payload["data"] = data
	}
	if errMsg != "" {
		payload["error"] = errMsg
	}
	return h.sendEvent(payload, peerID)
}

// RegisterHandle 实现 RoutineIO.
func (h *RoutineHub) RegisterHandle(childID string, handle *RoutineHandle) {
	h.Runtime.RegisterHandle(childID, handle)
}

// Go 起 goroutine(task 池).
func (h *RoutineHub) Go(fn func()) {
	h.Runtime.Go(fn)
}

// --- 运行时 routine 注册/移除 ---

// RegisterRoutine 运行时注册 routine(动态 per-agent skill routine 走此入口).
// 有 transport 时逐个发 catalog.register 等回执;无 transport 直接本地注册.
func (h *RoutineHub) RegisterRoutine(factories ...RoutineFactory) error {
	if h.Transport == nil {
		for _, f := range factories {
			h.Runtime.Registry.Register(f)
		}
		return nil
	}
	for _, f := range factories {
		reqID := NewCatalogReqID()
		fut := make(chan ackResult, 1)
		h.Runtime.RegisterRegisterFuture(reqID, fut)
		err := h.sendCatalogRegister(f, reqID, "")
		if err != nil {
			h.Runtime.PopRegisterFuture(reqID)
			return err
		}
		if err := waitAckWithTimeout(fut, HubAckTimeout); err != nil {
			return fmt.Errorf("%w: %v", RegisterError, err)
		}
		h.Runtime.Registry.Register(f)
	}
	return nil
}

// ReloadRoutine 运行时重载 routine(同名覆盖).
func (h *RoutineHub) ReloadRoutine(factories ...RoutineFactory) error {
	if h.Transport == nil {
		for _, f := range factories {
			h.Runtime.Registry.Register(f)
		}
		return nil
	}
	for _, f := range factories {
		reqID := NewCatalogReqID()
		fut := make(chan ackResult, 1)
		h.Runtime.RegisterReloadFuture(reqID, fut)
		err := h.sendCatalogReload(f, reqID, "")
		if err != nil {
			h.Runtime.PopReloadFuture(reqID)
			return err
		}
		if err := waitAckWithTimeout(fut, HubAckTimeout); err != nil {
			return fmt.Errorf("%w: %v", ReloadError, err)
		}
		h.Runtime.Registry.Register(f)
	}
	return nil
}

// DeregisterRoutine 运行时移除 routine.返回被移除的 factory.
func (h *RoutineHub) DeregisterRoutine(name string) (RoutineFactory, error) {
	if h.Transport == nil {
		return h.Runtime.Registry.Deregister(name), nil
	}
	reqID := NewCatalogReqID()
	fut := make(chan ackResult, 1)
	h.Runtime.RegisterDeregisterFuture(reqID, fut)
	if err := h.sendCatalogDeregister(name, reqID, ""); err != nil {
		h.Runtime.PopDeregisterFuture(reqID)
		return nil, err
	}
	if err := waitAckWithTimeout(fut, HubAckTimeout); err != nil {
		return nil, fmt.Errorf("%w: %v", DeregisterError, err)
	}
	return h.Runtime.PopDeregisterResult(reqID), nil
}

func (h *RoutineHub) sendCatalogRegister(f RoutineFactory, reqID, peerID string) error {
	payload := map[string]any{
		"event":      CatalogRegister,
		"name":       f.Name(),
		"is_passive": f.IsPassive(),
		"meta":       f.Meta(),
	}
	if reqID != "" {
		payload["req_id"] = reqID
	}
	return h.sendEvent(payload, peerID)
}

func (h *RoutineHub) sendCatalogReload(f RoutineFactory, reqID, peerID string) error {
	payload := map[string]any{
		"event":      CatalogReload,
		"name":       f.Name(),
		"is_passive": f.IsPassive(),
		"meta":       f.Meta(),
	}
	if reqID != "" {
		payload["req_id"] = reqID
	}
	return h.sendEvent(payload, peerID)
}

func (h *RoutineHub) sendCatalogDeregister(name, reqID, peerID string) error {
	payload := map[string]any{
		"event": CatalogDeregister,
		"name":  name,
	}
	if reqID != "" {
		payload["req_id"] = reqID
	}
	return h.sendEvent(payload, peerID)
}

func (h *RoutineHub) sendCatalogDeregisterCmdAck(reqID string, ok bool, errMsg string, peerID string) error {
	payload := map[string]any{
		"event":   CatalogDeregisterCmdAck,
		"req_id":  reqID,
		"ok":      ok,
	}
	if errMsg != "" {
		payload["error"] = errMsg
	}
	return h.sendEvent(payload, peerID)
}

// SendCatalogPush dial-in routine 连上后主动 push catalog 给 kernel.
// fire-and-forget(不阻塞等回执;_post_connect 在 recv loop 之前执行,阻塞等会死锁).
func (h *RoutineHub) SendCatalogPush(peerID string) error {
	reqID := NewCatalogReqID()
	routines := h.Query.BuildRoutines()
	payload := map[string]any{
		"event":    CatalogPush,
		"req_id":   reqID,
		"routines": routines,
		"modules":  h.Runtime.Modules,
		"hub_id":   h.HubID,
	}
	if err := h.sendEvent(payload, peerID); err != nil {
		return err
	}
	h.logger.Infof("catalog.push sent (req_id=%s, hub_id=%s): %d routines",
		reqID, h.HubID, len(routines))
	return nil
}

// --- 入站分发(transport 调)---

// DispatchInbound transport 收到一条入站命令 → 按 event 分发.
func (h *RoutineHub) DispatchInbound(peerID string, msg map[string]any) {
	event, _ := msg["event"].(string)
	switch event {
	case ModuleTreeEvent:
		// dial-in:kernel 推模块树拓扑(走 Stream);同步缓存.
		h.Query.CacheModuleTree(msg)
		return
	case RoutineGetRunningReply:
		// dial-out:routine.get_running 回执.
		if t, ok := h.Transport.(interface{ ResolveGetRunning(map[string]any) }); ok {
			t.ResolveGetRunning(msg)
		}
		return
	case RoutineGetModuleTreeReply:
		if t, ok := h.Transport.(interface{ ResolveGetModuleTree(map[string]any) }); ok {
			t.ResolveGetModuleTree(msg)
		}
		return
	case LifecycleStart:
		h.Runtime.Go(func() { h.Lifecycle.HandleStart(peerID, msg) })
	case LifecycleStop:
		h.Runtime.Go(func() { h.Lifecycle.HandleStop(peerID, msg) })
	case LifecycleDestroy:
		h.Runtime.Go(func() { h.Lifecycle.HandleDestroy(peerID, msg) })
	case LifecycleCreated:
		// kernel→server 命令(带 name):实例化+注册+建 inbox+auto_subscribe.
		if _, ok := msg["name"].(string); ok {
			h.Runtime.Go(func() { h.Lifecycle.HandleCreated(peerID, msg) })
		} else {
			h.Runtime.Go(func() { h.onInbound(msg) })
		}
	default:
		h.Runtime.Go(func() { h.onInbound(msg) })
	}
}

// OnPeerDown peer 断开(transport 通知)→ 强制清理该 peer 的所有 running instance.
func (h *RoutineHub) OnPeerDown(peerID string) {
	h.Runtime.Go(func() { h.Lifecycle.ForceStopPeer(peerID) })
}

// onInbound kernel→server 回向事件.按 event 查 inboundHandlers 分发表派发.
func (h *RoutineHub) onInbound(msg map[string]any) {
	event, _ := msg["event"].(string)
	h.inboundMu.RLock()
	handler, ok := h.inboundHandlers[event]
	h.inboundMu.RUnlock()
	if ok && handler != nil {
		handler(msg)
	}
}

// --- onInbound 各 event handler ---

func (h *RoutineHub) onLifecycleStarted(msg map[string]any) {
	cid, _ := msg["id"].(string)
	if handle := h.Runtime.GetHandle(cid); handle != nil {
		handle.NotifyStarted()
	}
}

func (h *RoutineHub) onLifecycleStopped(msg map[string]any) {
	cid, _ := msg["id"].(string)
	handle := h.Runtime.GetHandle(cid)
	if handle == nil {
		return
	}
	reason, _ := msg["reason"].(string)
	errMsg, _ := msg["error"].(string)
	if errMsg == "" && reason == ReasonError {
		errMsg = fmt.Sprintf("routine stopped with reason=%s", reason)
	}
	handle.NotifyDone(msg["result"], errMsg, reason)
	h.Runtime.PopHandle(cid)
}

func (h *RoutineHub) onRoutineRejected(msg map[string]any) {
	cid, _ := msg["child_id"].(string)
	errStr, _ := msg["error"].(string)
	if errStr == "" {
		errStr = "rejected"
	}
	op, _ := msg["op"].(string)
	handle := h.Runtime.GetHandle(cid)
	if op == "start" || op == "force_start" {
		if handle != nil {
			handle.RejectAck(errStr)
		}
		// 非 try_start 失败:清 created instance.
		isTry, _ := msg["try"].(bool)
		if !isTry && cid != "" {
			for prid := range h.Runtime.runningInstances {
				if endsWithRid(prid, cid) {
					h.Lifecycle.cleanup(prid)
					break
				}
			}
		}
	} else {
		if handle != nil {
			handle.RejectAck(errStr)
		}
	}
}

// endsWithRid 检查 prid 是否以 ":<rid>" 结尾.
func endsWithRid(prid, rid string) bool {
	if len(prid) <= len(rid)+1 {
		return false
	}
	return prid[len(prid)-len(rid)-1] == ':' && prid[len(prid)-len(rid):] == rid
}

func (h *RoutineHub) onRoutineSubmitted(msg map[string]any) {
	reqID, _ := msg["req_id"].(string)
	fut := h.Runtime.PopSubmitFuture(reqID)
	if fut == nil {
		return
	}
	select {
	case <-fut:
		return
	default:
	}
	if errStr, _ := msg["error"].(string); errStr != "" {
		fut <- submitResult{err: errStr}
		return
	}
	childID, _ := msg["child_id"].(string)
	var modules []string
	if mods, ok := msg["modules"].([]any); ok {
		modules = make([]string, 0, len(mods))
		for _, m := range mods {
			modules = append(modules, fmt.Sprint(m))
		}
	}
	fut <- submitResult{childID: childID, modules: modules}
}

func (h *RoutineHub) resolveAckFuture(reqID string, msg map[string]any, sentinel error, errDefault string) {
	fut := h.Runtime.PopAcquireFuture(reqID)
	if fut == nil {
		return
	}
	select {
	case <-fut:
		return
	default:
	}
	okV, _ := msg["ok"].(bool)
	if !okV {
		errStr, _ := msg["error"].(string)
		if errStr == "" {
			errStr = errDefault
		}
		fut <- ackResult{ok: false, err: errStr}
		return
	}
	fut <- ackResult{ok: true}
}

func (h *RoutineHub) onRoutineAcquired(msg map[string]any) {
	reqID, _ := msg["req_id"].(string)
	h.resolveAckFuture(reqID, msg, AcquireError, "acquire failed")
}

func (h *RoutineHub) onRoutineReleased(msg map[string]any) {
	reqID, _ := msg["req_id"].(string)
	h.resolveAckFuture(reqID, msg, ReleaseError, "release failed")
}

func (h *RoutineHub) onRoutineModuleLoaded(msg map[string]any) {
	reqID, _ := msg["req_id"].(string)
	fut := h.Runtime.PopLoadFuture(reqID)
	if fut == nil {
		return
	}
	select {
	case <-fut:
		return
	default:
	}
	okV, _ := msg["ok"].(bool)
	if !okV {
		errStr, _ := msg["error"].(string)
		if errStr == "" {
			errStr = "load_module failed"
		}
		fut <- ackResult{ok: false, err: errStr}
		return
	}
	fut <- ackResult{ok: true}
}

func (h *RoutineHub) onRoutineModuleUnloaded(msg map[string]any) {
	reqID, _ := msg["req_id"].(string)
	fut := h.Runtime.PopUnloadFuture(reqID)
	if fut == nil {
		return
	}
	select {
	case <-fut:
		return
	default:
	}
	okV, _ := msg["ok"].(bool)
	if !okV {
		errStr, _ := msg["error"].(string)
		if errStr == "" {
			errStr = "unload_module failed"
		}
		fut <- ackResult{ok: false, err: errStr}
		return
	}
	fut <- ackResult{ok: true}
}

func (h *RoutineHub) onCatalogRegistered(msg map[string]any) {
	reqID, _ := msg["req_id"].(string)
	fut := h.Runtime.PopRegisterFuture(reqID)
	if fut == nil {
		return
	}
	select {
	case <-fut:
		return
	default:
	}
	okV, _ := msg["ok"].(bool)
	if !okV {
		errStr, _ := msg["error"].(string)
		if errStr == "" {
			errStr = "register rejected by kernel"
		}
		fut <- ackResult{ok: false, err: errStr}
		return
	}
	fut <- ackResult{ok: true}
}

func (h *RoutineHub) onCatalogReloaded(msg map[string]any) {
	reqID, _ := msg["req_id"].(string)
	fut := h.Runtime.PopReloadFuture(reqID)
	if fut == nil {
		return
	}
	select {
	case <-fut:
		return
	default:
	}
	okV, _ := msg["ok"].(bool)
	if !okV {
		errStr, _ := msg["error"].(string)
		if errStr == "" {
			errStr = "reload rejected by kernel"
		}
		fut <- ackResult{ok: false, err: errStr}
		return
	}
	fut <- ackResult{ok: true}
}

func (h *RoutineHub) onCatalogDeregistered(msg map[string]any) {
	reqID, _ := msg["req_id"].(string)
	fut := h.Runtime.PopDeregisterFuture(reqID)
	if fut == nil {
		return
	}
	select {
	case <-fut:
		return
	default:
	}
	okV, _ := msg["ok"].(bool)
	if !okV {
		errStr, _ := msg["error"].(string)
		if errStr == "" {
			errStr = "deregister rejected by kernel"
		}
		fut <- ackResult{ok: false, err: errStr}
		return
	}
	fut <- ackResult{ok: true}
}

func (h *RoutineHub) onCatalogPushed(msg map[string]any) {
	registered, _ := msg["registered"].([]any)
	skipped, _ := msg["skipped"].([]any)
	reqID, _ := msg["req_id"].(string)
	if len(skipped) > 0 {
		h.logger.Warnf("catalog.pushed (req_id=%s): registered=%d, skipped=%d %v",
			reqID, len(registered), len(skipped), skipped)
	} else {
		h.logger.Infof("catalog.pushed (req_id=%s): registered=%d, skipped=0",
			reqID, len(registered))
	}
}

func (h *RoutineHub) onCatalogDeregisterCmd(msg map[string]any) {
	reqID, _ := msg["req_id"].(string)
	name, _ := msg["name"].(string)
	if name == "" {
		_ = h.sendCatalogDeregisterCmdAck(reqID, false, "name is required", "")
		return
	}
	removed := h.Runtime.Registry.Deregister(name)
	if reqID != "" {
		h.Runtime.SetDeregisterResult(reqID, removed)
	}
	_ = h.sendCatalogDeregisterCmdAck(reqID, true, "", "")
	removedName := "<nil>"
	if removed != nil {
		removedName = removed.Name()
	}
	h.logger.Infof("catalog.deregister.cmd: local dereg %s (removed=%s)", name, removedName)
}

func (h *RoutineHub) resolveSource(msg map[string]any) RoutineSource {
	src, _ := msg["source"].(map[string]any)
	sourceID, _ := src["id"].(string)
	if state := h.Runtime.GetCreated(sourceID); state != nil {
		return RoutineSource{ID: sourceID, Name: state.instance.Name()}
	}
	return RoutineSource{ID: sourceID}
}

func (h *RoutineHub) onMessageDelivered(msg map[string]any) {
	targetID, _ := msg["target_id"].(string)
	state := h.Runtime.GetCreated(targetID)
	if state == nil {
		return
	}
	source := h.resolveSource(msg)
	data := msg["data"]
	if mh, ok := state.instance.(MessageHandler); ok {
		h.Runtime.Go(func() { _ = mh.OnMessage(source, data) })
	}
}

func (h *RoutineHub) onMessageReqDelivered(msg map[string]any) {
	targetID, _ := msg["target_id"].(string)
	state := h.Runtime.GetCreated(targetID)
	if state == nil {
		return
	}
	source := h.resolveSource(msg)
	data, _ := msg["data"].(map[string]any)
	if data == nil {
		data = map[string]any{}
	}
	h.Runtime.Go(func() { h.serveRequest(state, data, source) })
}

func (h *RoutineHub) serveRequest(state *invocationState, data map[string]any, source RoutineSource) {
	event, _ := data[EnvelopeEvent].(string)
	handler, ok := state.requestHandlers[event]
	if !ok {
		return
	}
	result, err := handler(source, data)
	reply := map[string]any{
		EnvelopeReqID: data[EnvelopeReqID],
	}
	if err != nil {
		reply[EnvelopeOK] = false
		reply[EnvelopeError] = err.Error()
	} else {
		reply[EnvelopeOK] = true
		reply[EnvelopeData] = result
	}
	replyTo, _ := data[EnvelopeReplyTo].(string)
	_ = h.SendMessage([]string{replyTo}, MessageReqReply, reply, state.instance.Name(), state.peerID)
}

func (h *RoutineHub) onMessageReqReplyDelivered(msg map[string]any) {
	data, _ := msg["data"].(map[string]any)
	if data == nil {
		return
	}
	reqID, _ := data[EnvelopeReqID].(string)
	fut := h.Runtime.PopReqFuture(reqID)
	if fut == nil {
		return
	}
	select {
	case <-fut:
		return
	default:
	}
	okV, _ := data[EnvelopeOK].(bool)
	if okV {
		fut <- reqReply{ok: true, data: data[EnvelopeData]}
	} else {
		errStr, _ := data[EnvelopeError].(string)
		fut <- reqReply{ok: false, err: errStr}
	}
}

func (h *RoutineHub) onMessageStreamOpenDelivered(msg map[string]any) {
	targetID, _ := msg["target_id"].(string)
	state := h.Runtime.GetCreated(targetID)
	if state == nil {
		return
	}
	source := h.resolveSource(msg)
	data, _ := msg["data"].(map[string]any)
	if data == nil {
		data = map[string]any{}
	}
	h.Runtime.Go(func() { h.serveStream(state, data, source) })
}

func (h *RoutineHub) serveStream(state *invocationState, data map[string]any, source RoutineSource) {
	event, _ := data[EnvelopeEvent].(string)
	handler, ok := state.streamHandlers[event]
	if !ok {
		return
	}
	streamID, _ := data[EnvelopeStreamID].(string)
	replyTo, _ := data[EnvelopeReplyTo].(string)
	writer := &providerStreamWriter{
		hub:        h,
		state:      state,
		streamID:   streamID,
		replyTo:    replyTo,
	}
	if err := handler(source, data, writer.toStreamWriter()); err != nil {
		writer.sendEOF("error", err.Error())
		return
	}
	writer.sendEOF("", "")
}

func (h *RoutineHub) onMessageStreamDataDelivered(msg map[string]any) {
	data, _ := msg["data"].(map[string]any)
	if data == nil {
		return
	}
	streamID, _ := data[EnvelopeStreamID].(string)
	reader := h.Runtime.GetStreamReader(streamID)
	if reader == nil {
		return
	}
	if eof, _ := data[EnvelopeEOF].(string); eof != "" {
		errStr, _ := data[EnvelopeError].(string)
		reader.feedEOF(eof, errStr)
		h.Runtime.PopStreamReader(streamID)
	} else {
		reader.feedChunk(data[EnvelopeChunk])
	}
}

func (h *RoutineHub) onMessageStreamCancelDelivered(msg map[string]any) {
	data, _ := msg["data"].(map[string]any)
	if data == nil {
		return
	}
	// stream cancel 在 provider 侧通过 ctx.Done 处理,这里只记录.
}

func (h *RoutineHub) onPubsubDelivered(msg map[string]any) {
	subscriberID, _ := msg["subscriber_id"].(string)
	topic, _ := msg["topic"].(string)
	namespace, _ := msg["namespace"].(string)
	data := msg["data"]
	source := h.resolveSource(msg)
	handler := h.Runtime.GetSubscriberHandler(subscriberID, namespace, topic)
	if handler == nil {
		return
	}
	h.Runtime.Go(func() {
		if err := handler(source, data); err != nil {
			h.logger.Errorf("subscriber %s @subscribe(%s) failed: %v", subscriberID, topic, err)
		}
	})
}

func (h *RoutineHub) onYielded(msg map[string]any) {
	childID, _ := msg["id"].(string)
	handle := h.Runtime.GetHandle(childID)
	if handle == nil {
		return
	}
	isFinal, _ := msg["is_final"].(bool)
	errMsg, _ := msg["error"].(string)
	var data any
	if !isFinal {
		data = msg["data"]
	}
	handle.OnYieldChunk(data, isFinal, errMsg)
}

// --- 入口:StartServer / StartClient ---

// StartServer dial-out 模型:routine 当 grpc server,kernel 主动 dial 进来.
func StartServer(registry *Registry, modules []string, address string, hubID string) error {
	transport := NewGrpcServerTransport(address)
	hub, err := NewRoutineHub(registry, modules, transport, hubID)
	if err != nil {
		return err
	}
	transport.Attach(hub)
	if err := transport.Start(); err != nil {
		return err
	}
	defer transport.Stop()
	return transport.Wait()
}

// StartClient dial-in 模型:routine 当 grpc client,主动 dial kernel server.
func StartClient(registry *Registry, modules []string, address string, hubID string) error {
	transport := NewGrpcClientTransport(address)
	hub, err := NewRoutineHub(registry, modules, transport, hubID)
	if err != nil {
		return err
	}
	transport.Attach(hub)
	if err := transport.Start(); err != nil {
		return err
	}
	defer transport.Stop()
	return transport.Wait()
}

func waitAckWithTimeout(fut <-chan ackResult, timeout time.Duration) error {
	select {
	case res := <-fut:
		if !res.ok {
			return errors.New(res.err)
		}
		return nil
	case <-time.After(timeout):
		return errors.New("ack timeout")
	}
}

// providerStreamWriter stream provider 侧的 writer,把 chunk 经 message.stream_data 发回 caller.
type providerStreamWriter struct {
	hub      *RoutineHub
	state    *invocationState
	streamID string
	replyTo  string
}

func (w *providerStreamWriter) toStreamWriter() *StreamWriter {
	// 复用 StreamWriter 接口但通过 hack:provider 走 message.stream_data 通路.
	// 简化:返回一个空的 StreamWriter,实际 provider handler 应该直接调 w.Write / w.sendEOF.
	return &StreamWriter{}
}

// Write 写一个 chunk.走 message.stream_data.
func (w *providerStreamWriter) Write(data any) error {
	payload := map[string]any{
		EnvelopeStreamID: w.streamID,
		EnvelopeChunk:    data,
	}
	return w.hub.SendMessage([]string{w.replyTo}, MessageStreamData, payload,
		w.state.instance.Name(), w.state.peerID)
}

func (w *providerStreamWriter) sendEOF(eof, errMsg string) {
	payload := map[string]any{
		EnvelopeStreamID: w.streamID,
	}
	if eof != "" {
		payload[EnvelopeEOF] = eof
	}
	if errMsg != "" {
		payload[EnvelopeError] = errMsg
	}
	_ = w.hub.SendMessage([]string{w.replyTo}, MessageStreamData, payload,
		w.state.instance.Name(), w.state.peerID)
}
