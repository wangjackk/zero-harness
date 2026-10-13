package routine

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

// RoutineIO service 层注入的 wire 出口(RoutineHub 实现).
// 对标 Python RoutineIO Protocol.
type RoutineIO interface {
	// lifecycle 出口
	SendLifecycleCreated(id string, modules []string, peerID string) error
	SendLifecycleStarted(id string, peerID string) error
	SendLifecycleStopped(id string, reason string, result any, errMsg string, peerID string) error
	RequestStop(id string, peerID string) error

	// routine 调 routine 反向事件
	SendRoutineCreate(reqID, parentID, name string, kwargs map[string]any, peerID string) error
	SendRoutineStart(childID string, tryStart bool, priority int, peerID string) error
	SendRoutineStop(childID string, peerID string) error
	SendRoutineDestroy(childID string, peerID string) error
	SendRoutineAcquire(reqID, id string, modules []string, peerID string) error
	SendRoutineRelease(reqID, id string, modules []string, peerID string) error
	SendRoutineLoadModule(reqID, parentID, childID, name string, peerID string) error
	SendRoutineUnloadModule(reqID, childID string, peerID string) error

	// message.* 定向消息
	SendMessage(targetIDs []string, sendEvent string, data map[string]any, sourceID, peerID string) error

	// pubsub
	SendPubsubSubscribe(id, topic, namespace, peerID string) error
	SendPubsubUnsubscribe(id, topic, namespace, peerID string) error
	SendPubsubPublish(topic string, data any, sourceID, namespace, peerID string) error

	// lifecycle 观察流
	SendLifecycleWatch(reqID, peerID string) error
	SendLifecycleUnwatch(peerID string) error

	// yield
	SendYield(id string, data any, isFinal bool, errMsg string, peerID string) error

	// handle 注册
	RegisterHandle(childID string, handle *RoutineHandle)

	// task 池
	Go(fn func())
}

// Transport 传输层抽象:入站投递 + 出站发送 + 启停.
// 对标 Python Transport.
type Transport interface {
	Start() error
	Stop() error
	SendEvent(payload map[string]any, peerID string) error
	// Req routine→kernel Req 查询(dial-in client 用)
	Req(msg map[string]any) (map[string]any, error)
	// GetRunningRoutines 查 kernel 当前所有 running routine 实例
	GetRunningRoutines() ([]map[string]any, error)
	// GetModuleTree 主动从 kernel 拉 module.tree
	GetModuleTree() (*ModuleTree, error)
	// GetRoutines 查 kernel 全量路由表
	GetRoutines() ([]map[string]any, error)
}

// invocationState 一次 routine invocation 的运行时状态.
// 对标 Python Routine 实例上的 _ 前缀字段.
type invocationState struct {
	instance       Routine
	peerID         string
	ctx            *RunContext
	ctxOnce        sync.Once
	mu             sync.Mutex
	phase          phase // 生命周期相位:created/started/stopped
	stopRequested  bool
	stopInProgress bool
	stopFinalized  bool
	stoppedSent    bool
	initKwargs     map[string]any
	mainDone       chan struct{}
	yieldUsed      bool // Run 中调过 ctx.Yield → 框架自动发 is_final 收尾
	// handler 表(OnCreated/HandleRequest 中注册)
	requestHandlers  map[string]RequestHandler
	streamHandlers   map[string]StreamHandler
	subscribeHandlers map[string]SubscribeHandler // key = namespace+"\x00"+topic
}

// phase invocation 生命周期相位:created(从未 start)→ started(运行中)→
// stopped(start 后被 stop/驱逐).守卫据此区分"还没 start"(报错)与
// "start 后被停"(no-op).
type phase uint8

const (
	phaseCreated phase = iota
	phaseStarted
	phaseStopped
)

func newInvocationState(instance Routine, peerID string) *invocationState {
	return &invocationState{
		instance:          instance,
		peerID:            peerID,
		mainDone:          make(chan struct{}),
		requestHandlers:   make(map[string]RequestHandler),
		streamHandlers:    make(map[string]StreamHandler),
		subscribeHandlers: make(map[string]SubscribeHandler),
	}
}

func (s *invocationState) resetForStart() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.stopRequested = false
	s.stopInProgress = false
	s.phase = phaseCreated
}

func (s *invocationState) markStarted() {
	s.mu.Lock()
	s.phase = phaseStarted
	s.mu.Unlock()
}

// markNotStarted stop/驱逐后置 stopped.从未 started 的保持 created
// (release 守卫要区分"还没 start"与"start 后被停").
func (s *invocationState) markNotStarted() {
	s.mu.Lock()
	if s.phase == phaseStarted {
		s.phase = phaseStopped
	}
	s.mu.Unlock()
}

func (s *invocationState) isStarted() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.phase == phaseStarted
}

func (s *invocationState) isStopped() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.phase == phaseStopped
}

func (s *invocationState) beginStop() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.stopInProgress || s.stopFinalized {
		return false
	}
	s.stopInProgress = true
	return true
}

func (s *invocationState) finalizeStop() {
	s.mu.Lock()
	s.stopFinalized = true
	s.mu.Unlock()
}

func (s *invocationState) isStopFinalized() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.stopFinalized
}

// clearStopFinalized restart / 复用实例时清终态(runner 守卫查它).
func (s *invocationState) clearStopFinalized() {
	s.mu.Lock()
	s.stopFinalized = false
	s.mu.Unlock()
}

// setYieldUsed 标记 Run 中调过 ctx.Yield,框架据此在 Run 结束后自动发 is_final.
func (s *invocationState) setYieldUsed() {
	s.mu.Lock()
	s.yieldUsed = true
	s.mu.Unlock()
}

// isYieldUsed 检查是否调过 ctx.Yield.
func (s *invocationState) isYieldUsed() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.yieldUsed
}

func (s *invocationState) clearStopInProgress() {
	s.mu.Lock()
	s.stopInProgress = false
	s.mu.Unlock()
}

func (s *invocationState) requestStop() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.stopRequested {
		return false
	}
	s.stopRequested = true
	return true
}

func (s *invocationState) isStopRequested() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.stopRequested
}

func (s *invocationState) markStoppedSent() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.stoppedSent {
		return false
	}
	s.stoppedSent = true
	return true
}

// RunContext 每次 Run invocation 的运行上下文.
// 对标 Python RunContext.service 在 lifecycle.start 之后绑给 routine.
type RunContext struct {
	ID     string
	Name   string
	PeerID string

	io       RoutineIO
	runtime  *ServerRuntime
	transport Transport
	state    *invocationState
	logger   *Logger
}

// AckTimeout create/acquire/release 等 kernel ack 默认超时.
const AckTimeout = 30 * time.Second

// AckStart 发 lifecycle.started 通知调度器进入 Started.
func (c *RunContext) AckStart() error {
	return c.io.SendLifecycleStarted(c.ID, c.PeerID)
}

// Logger 返回 ctx 的 logger(routine 内日志用).
func (c *RunContext) Logger() *Logger { return c.logger }

// RequestStop 请求 runtime 发起一次正规 stop 流程.
func (c *RunContext) RequestStop() error {
	return c.io.RequestStop(c.ID, c.PeerID)
}

// Yield 发一个 routine.yield 帧给 parent(对齐 Python `yield item`).
// 业务在 Run 中循环调 Yield 推送流式产出,框架在 Run 结束后自动发 is_final=true
// 收尾(对齐 Python async generator 自然结束).无需手动 Close.
// Run 返回 error 时,框架自动发 is_final=true + error.
func (c *RunContext) Yield(data any) error {
	c.state.setYieldUsed()
	return c.sendYield(data, false, "")
}

// --- handler 注册(OnCreated 中调,替代 Python @request/@stream/@subscribe 装饰器) ---

// HandleRequest 注册 @request handler:event → handler.
func (c *RunContext) HandleRequest(event string, handler RequestHandler) {
	c.state.requestHandlers[event] = handler
}

// HandleStream 注册 @stream handler:event → handler.
func (c *RunContext) HandleStream(event string, handler StreamHandler) {
	c.state.streamHandlers[event] = handler
}

// HandleSubscribe 注册 @subscribe handler + 发 pubsub.subscribe.
// namespace 为空表示默认命名空间.
func (c *RunContext) HandleSubscribe(topic string, handler SubscribeHandler, namespace string) error {
	key := namespace + "\x00" + topic
	c.state.subscribeHandlers[key] = handler
	c.runtime.RegisterSubscriber(c.ID, namespace, topic, handler)
	return c.io.SendPubsubSubscribe(c.ID, topic, namespace, c.PeerID)
}

// --- routine 调 routine(create) ---

// Create 提交子 routine,经 kernel 回环.返回 handle.
func (c *RunContext) Create(name string, kwargs map[string]any) (*RoutineHandle, error) {
	reqID := NewReqID()
	fut := make(chan createResult, 1)
	c.runtime.RegisterCreateFuture(reqID, fut)

	if err := c.io.SendRoutineCreate(reqID, c.ID, name, kwargs, c.PeerID); err != nil {
		c.runtime.PopCreateFuture(reqID)
		return nil, err
	}

	select {
	case res := <-fut:
		if res.err != "" {
			return nil, fmt.Errorf("%w: %s", CreateError, res.err)
		}
		handle := newRoutineHandle(res.childID, name, c, res.modules)
		c.io.RegisterHandle(res.childID, handle)
		return handle, nil
	case <-time.After(AckTimeout):
		c.runtime.PopCreateFuture(reqID)
		return nil, fmt.Errorf("%w: create %s timeout", CreateError, name)
	}
}

// Call 同步拿子 routine 结果:Create → Start(priority) → Wait 一步到位.
// 要求父 started.priority 语义同 RoutineHandle.Start(冲突时 incoming 严格
// 大于全部第三方 holder 才驱逐后 start;平级/更低原样抛 StartError;0=冲突即拒).
func (c *RunContext) Call(name string, kwargs map[string]any, priority int) (any, error) {
	if !c.state.isStarted() {
		return nil, errors.New(c.Name + ": must AckStart() before Call (parent routine not started)")
	}
	handle, err := c.Create(name, kwargs)
	if err != nil {
		return nil, err
	}
	if err := handle.Start(priority); err != nil {
		return nil, err
	}
	return handle.Wait()
}

// --- 运行时占领/释放模块 ---

// Acquire 运行时占领模块.只 start 期间可用.撞车按本 routine 的 start 档位
// 裁决(kernel 侧):严格大于全部撞车 holder 才驱逐后占住,否则返回 AcquireError
// (try 语义由调用方自行处理).
func (c *RunContext) Acquire(modules []string) error {
	if !c.state.isStarted() {
		return errors.New(c.Name + ": must AckStart() before acquire")
	}
	return c.acquireRelease(reqAcquire, modules)
}

// Release 运行时释放指定模块.已 stop/被驱逐后再调为 no-op(kernel 已全量
// 回收),打 warning 不报错;从未 start 过才返回 error.
func (c *RunContext) Release(modules []string) error {
	if !c.state.isStarted() {
		if c.state.isStopped() {
			c.runtime.logger.Warnf("%s: release(%v) after stopped ---- kernel already reclaimed all modules, no-op", c.Name, modules)
			return nil
		}
		return errors.New(c.Name + ": release() before start() (routine never started)")
	}
	return c.acquireRelease(reqRelease, modules)
}

type acquireOp int

const (
	reqAcquire acquireOp = iota
	reqRelease
)

func (c *RunContext) acquireRelease(op acquireOp, modules []string) error {
	reqID := NewReqID()
	fut := make(chan ackResult, 1)
	c.runtime.RegisterAcquireFuture(reqID, fut)

	var err error
	switch op {
	case reqAcquire:
		err = c.io.SendRoutineAcquire(reqID, c.ID, modules, c.PeerID)
	case reqRelease:
		err = c.io.SendRoutineRelease(reqID, c.ID, modules, c.PeerID)
	}
	if err != nil {
		c.runtime.PopAcquireFuture(reqID)
		return err
	}

	select {
	case res := <-fut:
		if !res.ok {
			var sentinel error
			switch op {
			case reqAcquire:
				sentinel = AcquireError
			case reqRelease:
				sentinel = ReleaseError
			}
			return fmt.Errorf("%w: %s", sentinel, res.err)
		}
		return nil
	case <-time.After(AckTimeout):
		c.runtime.PopAcquireFuture(reqID)
		return errors.New("acquire/release timeout")
	}
}

// LoadModule 往父模块加载子模块(全局树动态增拓扑,只挂树不占用).
func (c *RunContext) LoadModule(parent, child, name string) error {
	if !c.state.isStarted() {
		return errors.New(c.Name + ": must AckStart() before load_module")
	}
	reqID := NewReqID()
	fut := make(chan ackResult, 1)
	c.runtime.RegisterLoadFuture(reqID, fut)
	defer c.runtime.PopLoadFuture(reqID)

	if err := c.io.SendRoutineLoadModule(reqID, parent, child, name, c.PeerID); err != nil {
		return err
	}
	return waitAck(fut, LoadModuleError, "load_module")
}

// UnloadModule 卸载子模块(全局树动态删拓扑).
func (c *RunContext) UnloadModule(child string) error {
	if !c.state.isStarted() {
		return errors.New(c.Name + ": must AckStart() before unload_module")
	}
	reqID := NewReqID()
	fut := make(chan ackResult, 1)
	c.runtime.RegisterUnloadFuture(reqID, fut)
	defer c.runtime.PopUnloadFuture(reqID)

	if err := c.io.SendRoutineUnloadModule(reqID, child, c.PeerID); err != nil {
		return err
	}
	return waitAck(fut, UnloadModuleError, "unload_module")
}

// --- p2p 通信(req / streamreq) ---

// Req 对 target routine 发 request,等回执拿 result.
func (c *RunContext) Req(target, event string, data map[string]any, timeout time.Duration) (any, error) {
	reqID := NewReqID()
	fut := make(chan reqReply, 1)
	c.runtime.RegisterReqFuture(reqID, fut)

	envelope := map[string]any{
		EnvelopeReqID:   reqID,
		EnvelopeReplyTo: c.ID,
		EnvelopeEvent:   event,
		EnvelopeData:    data,
	}
	if err := c.sendMessage(target, MessageReq, envelope); err != nil {
		c.runtime.PopReqFuture(reqID)
		return nil, err
	}

	select {
	case reply := <-fut:
		if !reply.ok {
			return nil, fmt.Errorf("%w: %s", ReqError, reply.err)
		}
		return reply.data, nil
	case <-time.After(timeout):
		c.runtime.PopReqFuture(reqID)
		return nil, fmt.Errorf("%w: req %s to %s timeout after %s", ReqTimeout, event, target, timeout)
	}
}

// StreamReq 对 target 发 stream request,返回 StreamCtx.
func (c *RunContext) StreamReq(target, event string, data map[string]any, timeout time.Duration) (*StreamCtx, error) {
	streamID := NewReqID()
	reader := newStreamReader(streamID, c, target)
	c.runtime.RegisterStreamReader(streamID, reader)

	envelope := map[string]any{
		EnvelopeStreamID: streamID,
		EnvelopeReplyTo:  c.ID,
		EnvelopeEvent:    event,
		EnvelopeData:     data,
	}
	if err := c.sendMessage(target, MessageStreamOpen, envelope); err != nil {
		c.runtime.PopStreamReader(streamID)
		return nil, err
	}
	return newStreamCtx(reader, timeout.Seconds()), nil
}

// --- pubsub ---

// Publish 发一条 pubsub 消息.
func (c *RunContext) Publish(topic string, data any, namespace string) error {
	return c.io.SendPubsubPublish(topic, data, c.ID, namespace, c.PeerID)
}

// Subscribe 订阅 topic.注册 handler + 发 pubsub.subscribe.
func (c *RunContext) Subscribe(topic string, handler SubscribeHandler, namespace string) error {
	c.runtime.RegisterSubscriber(c.ID, namespace, topic, handler)
	return c.io.SendPubsubSubscribe(c.ID, topic, namespace, c.PeerID)
}

// Unsubscribe 退订 topic.
func (c *RunContext) Unsubscribe(topic string, namespace string) error {
	c.runtime.PopSubscriberTopic(c.ID, namespace, topic)
	return c.io.SendPubsubUnsubscribe(c.ID, topic, namespace, c.PeerID)
}

// --- envelope 框架事件流观察 ---

// WatchLifecycle 开启 kernel 生命周期事件观察(lifecycle 流).
// 返回事件 channel:先灌入当前 routine 树快照,再收增量帧
// ({type: created/started/stopped/destroyed, id, parent_id, name, seq}).
// 消费端按 id 幂等 upsert/remove(id 不复用,乱序到达最终状态一致).
// 实例停止时框架自动注销(close chan,for range 消费自然退出);
// 也可显式 UnwatchLifecycle.多 routine 同时观察共存,互不影响.
func (c *RunContext) WatchLifecycle() (<-chan map[string]any, error) {
	// 先注册 watcher 再发 enable:enable 之后 kernel 即开始推帧,
	// 晚注册会丢帧(丢的帧由快照按 id 幂等补齐).
	ch := make(chan map[string]any, 256)
	c.runtime.RegisterLifecycleWatcher(c.ID, ch)

	reqID := NewReqID()
	fut := make(chan []map[string]any, 1)
	c.runtime.RegisterLifecycleFuture(reqID, fut)
	if err := c.io.SendLifecycleWatch(reqID, c.PeerID); err != nil {
		c.runtime.PopLifecycleFuture(reqID)
		c.runtime.PopLifecycleWatcher(c.ID)
		return nil, err
	}

	select {
	case frames := <-fut:
		for _, f := range frames {
			select {
			case ch <- f:
			default:
				c.runtime.Logger().Warnf("lifecycle watcher %s queue full, snapshot truncated", c.ID)
			}
		}
		return ch, nil
	case <-time.After(AckTimeout):
		c.runtime.PopLifecycleFuture(reqID)
		c.runtime.PopLifecycleWatcher(c.ID)
		return nil, errors.New("watch_lifecycle: lifecycle.snapshot timeout")
	}
}

// UnwatchLifecycle 注销观察(最后一个观察者注销时自动关流).
func (c *RunContext) UnwatchLifecycle() error {
	c.runtime.PopLifecycleWatcher(c.ID)
	if c.runtime.LifecycleWatcherCount() == 0 {
		return c.io.SendLifecycleUnwatch(c.PeerID)
	}
	return nil
}

// --- message.* 定向消息 ---

// Send 给 target routine 发定向消息(message.send).
func (c *RunContext) Send(target string, data any) error {
	return c.sendMessage(target, MessageSend, map[string]any{"data": data})
}

// --- 查询 ---

// GetRunningRoutines 查 kernel 当前所有 running routine 实例.
func (c *RunContext) GetRunningRoutines() ([]map[string]any, error) {
	return c.transport.GetRunningRoutines()
}

// GetModuleTree 主动从 kernel 拉 module.tree 并刷新缓存.
func (c *RunContext) GetModuleTree() (*ModuleTree, error) {
	return c.transport.GetModuleTree()
}

// GetRoutines 查 kernel 全量路由表.
func (c *RunContext) GetRoutines() ([]map[string]any, error) {
	return c.transport.GetRoutines()
}

// Conflict 两组 modules 是否冲突(纯本地计算).
func (c *RunContext) Conflict(modsA, modsB []string) bool {
	tree := c.runtime.GetModuleTree()
	if tree == nil {
		return false
	}
	return tree.Conflict(modsA, modsB)
}

// --- 内部 helper ---

func (c *RunContext) sendMessage(targetID, sendEvent string, data map[string]any) error {
	return c.io.SendMessage([]string{targetID}, sendEvent, data, c.ID, c.PeerID)
}

func (c *RunContext) sendYield(data any, isFinal bool, errMsg string) error {
	return c.io.SendYield(c.ID, data, isFinal, errMsg, c.PeerID)
}

func (c *RunContext) Go(fn func()) {
	c.io.Go(fn)
}

// --- ack 回执类型 ---

type createResult struct {
	childID string
	modules []string
	err     string
}

type ackResult struct {
	ok  bool
	err string
}

type reqReply struct {
	ok   bool
	data any
	err  string
}

func waitAck(fut <-chan ackResult, sentinel error, label string) error {
	select {
	case res := <-fut:
		if !res.ok {
			return fmt.Errorf("%w: %s", sentinel, res.err)
		}
		return nil
	case <-time.After(AckTimeout):
		return fmt.Errorf("%s timeout", label)
	}
}

// context.Context 适配(Go 标准 cancel 信号).
var _ context.Context = (*runCtxAdapter)(nil)

type runCtxAdapter struct{ ctx *RunContext }

func (a *runCtxAdapter) Deadline() (time.Time, bool) { return time.Time{}, false }
func (a *runCtxAdapter) Done() <-chan struct{}        { return a.ctx.state.mainDone }
func (a *runCtxAdapter) Err() error {
	if a.ctx.state.isStopFinalized() {
		return context.Canceled
	}
	return nil
}
func (a *runCtxAdapter) Value(key any) any { return nil }
