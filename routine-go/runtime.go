package routine

import "sync"

// ServerRuntime runtime 状态:routine 注册表,运行实例表,peer 出站队列,task 池.
// 对标 Python ServerRuntime.
type ServerRuntime struct {
	Registry     *Registry
	Modules      []string
	moduleTree   *ModuleTree
	moduleTreeMu sync.RWMutex
	logger       *Logger

	mu               sync.Mutex
	runningInstances map[string]*invocationState // prid → state

	// future 表
	submitFutures    map[string]chan submitResult
	acquireFutures   map[string]chan ackResult
	loadFutures      map[string]chan ackResult
	unloadFutures    map[string]chan ackResult
	registerFutures  map[string]chan ackResult
	reloadFutures    map[string]chan ackResult
	deregisterFutures map[string]chan ackResult
	deregisterResults map[string]RoutineFactory

	// handle 表
	handles map[string]*RoutineHandle

	// created instance 按 rid(p2p 路由用)
	createdByRid map[string]*invocationState

	// req 回执
	reqFutures map[string]chan reqReply

	// streamreq reader 表
	streamReaders map[string]*StreamReader

	// pubsub 订阅者:subscriber_id → {(namespace, topic): handler}
	subscribers map[string]map[string]SubscribeHandler

	// task 池
	wg sync.WaitGroup
}

// NewServerRuntime 创建 runtime.
func NewServerRuntime(registry *Registry, modules []string) *ServerRuntime {
	_ = GetLogger() // 确保单例 logger 已初始化
	return &ServerRuntime{
		Registry:          registry,
		Modules:           modules,
		logger:            GetLogger().Named("RoutineHub"),
		runningInstances:  make(map[string]*invocationState),
		submitFutures:     make(map[string]chan submitResult),
		acquireFutures:    make(map[string]chan ackResult),
		loadFutures:       make(map[string]chan ackResult),
		unloadFutures:     make(map[string]chan ackResult),
		registerFutures:   make(map[string]chan ackResult),
		reloadFutures:     make(map[string]chan ackResult),
		deregisterFutures: make(map[string]chan ackResult),
		deregisterResults: make(map[string]RoutineFactory),
		handles:           make(map[string]*RoutineHandle),
		createdByRid:      make(map[string]*invocationState),
		reqFutures:        make(map[string]chan reqReply),
		streamReaders:     make(map[string]*StreamReader),
		subscribers:       make(map[string]map[string]SubscribeHandler),
	}
}

// Logger 返回 runtime 的 logger.
func (r *ServerRuntime) Logger() *Logger { return r.logger }

// GetModuleTree 返回缓存的模块树(可能 nil).
func (r *ServerRuntime) GetModuleTree() *ModuleTree {
	r.moduleTreeMu.RLock()
	defer r.moduleTreeMu.RUnlock()
	return r.moduleTree
}

// SetModuleTree 更新缓存的模块树.
func (r *ServerRuntime) SetModuleTree(tree *ModuleTree) {
	r.moduleTreeMu.Lock()
	r.moduleTree = tree
	r.moduleTreeMu.Unlock()
}

// Go 起 goroutine(task 池).
func (r *ServerRuntime) Go(fn func()) {
	r.wg.Add(1)
	go func() {
		defer r.wg.Done()
		fn()
	}()
}

// Wait 等所有 goroutine 完成.
func (r *ServerRuntime) Wait() { r.wg.Wait() }

// PrintSummary 打印一行 routine 注册统计.
func (r *ServerRuntime) PrintSummary() {
	factories := r.Registry.All()
	enabled := 0
	passive := 0
	for _, f := range factories {
		enabled++
		if f.IsPassive() {
			passive++
		}
	}
	r.logger.Infof("%d routines · %d enabled · %d passive", len(factories), enabled, passive)
}

// --- running instance 管理 ---

// ResolveInstance 同 prid 已有实例则复用(restart 语义),否则新建.
func (r *ServerRuntime) ResolveInstance(prid string, factory RoutineFactory) (Routine, *invocationState) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if state, ok := r.runningInstances[prid]; ok {
		return state.instance, state
	}
	inst := factory.Create()
	state := newInvocationState(inst, "")
	r.runningInstances[prid] = state
	return inst, state
}

// RegisterInstance 注册 running instance.
func (r *ServerRuntime) RegisterInstance(prid string, state *invocationState) {
	r.mu.Lock()
	r.runningInstances[prid] = state
	r.mu.Unlock()
}

// GetInstance 获取 running instance by prid.
func (r *ServerRuntime) GetInstance(prid string) *invocationState {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.runningInstances[prid]
}

// PopInstance 移除并返回 running instance.
func (r *ServerRuntime) PopInstance(prid string) *invocationState {
	r.mu.Lock()
	defer r.mu.Unlock()
	s := r.runningInstances[prid]
	delete(r.runningInstances, prid)
	return s
}

// InstancesByPeer 返回指定 peer 的所有 instance prid 列表.
func (r *ServerRuntime) InstancesByPeer(peerID string) []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	prefix := peerID + ":"
	var out []string
	for prid := range r.runningInstances {
		if len(prid) > len(prefix) && prid[:len(prefix)] == prefix {
			out = append(out, prid)
		}
	}
	return out
}

// --- created instance 按 rid ---

func (r *ServerRuntime) RegisterCreated(rid string, state *invocationState) {
	r.mu.Lock()
	r.createdByRid[rid] = state
	r.mu.Unlock()
}

func (r *ServerRuntime) GetCreated(rid string) *invocationState {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.createdByRid[rid]
}

func (r *ServerRuntime) PopCreated(rid string) *invocationState {
	r.mu.Lock()
	defer r.mu.Unlock()
	s := r.createdByRid[rid]
	delete(r.createdByRid, rid)
	return s
}

// --- submit future 表 ---

func (r *ServerRuntime) RegisterSubmitFuture(reqID string, ch chan submitResult) {
	r.mu.Lock()
	r.submitFutures[reqID] = ch
	r.mu.Unlock()
}

func (r *ServerRuntime) PopSubmitFuture(reqID string) chan submitResult {
	r.mu.Lock()
	defer r.mu.Unlock()
	ch := r.submitFutures[reqID]
	delete(r.submitFutures, reqID)
	return ch
}

// --- acquire/release future 表 ---

func (r *ServerRuntime) RegisterAcquireFuture(reqID string, ch chan ackResult) {
	r.mu.Lock()
	r.acquireFutures[reqID] = ch
	r.mu.Unlock()
}

func (r *ServerRuntime) PopAcquireFuture(reqID string) chan ackResult {
	r.mu.Lock()
	defer r.mu.Unlock()
	ch := r.acquireFutures[reqID]
	delete(r.acquireFutures, reqID)
	return ch
}

// --- load/unload future 表 ---

func (r *ServerRuntime) RegisterLoadFuture(reqID string, ch chan ackResult) {
	r.mu.Lock()
	r.loadFutures[reqID] = ch
	r.mu.Unlock()
}

func (r *ServerRuntime) PopLoadFuture(reqID string) chan ackResult {
	r.mu.Lock()
	defer r.mu.Unlock()
	ch := r.loadFutures[reqID]
	delete(r.loadFutures, reqID)
	return ch
}

func (r *ServerRuntime) RegisterUnloadFuture(reqID string, ch chan ackResult) {
	r.mu.Lock()
	r.unloadFutures[reqID] = ch
	r.mu.Unlock()
}

func (r *ServerRuntime) PopUnloadFuture(reqID string) chan ackResult {
	r.mu.Lock()
	defer r.mu.Unlock()
	ch := r.unloadFutures[reqID]
	delete(r.unloadFutures, reqID)
	return ch
}

// --- catalog register/reload/deregister future 表 ---

func (r *ServerRuntime) RegisterRegisterFuture(reqID string, ch chan ackResult) {
	r.mu.Lock()
	r.registerFutures[reqID] = ch
	r.mu.Unlock()
}

func (r *ServerRuntime) PopRegisterFuture(reqID string) chan ackResult {
	r.mu.Lock()
	defer r.mu.Unlock()
	ch := r.registerFutures[reqID]
	delete(r.registerFutures, reqID)
	return ch
}

func (r *ServerRuntime) RegisterReloadFuture(reqID string, ch chan ackResult) {
	r.mu.Lock()
	r.reloadFutures[reqID] = ch
	r.mu.Unlock()
}

func (r *ServerRuntime) PopReloadFuture(reqID string) chan ackResult {
	r.mu.Lock()
	defer r.mu.Unlock()
	ch := r.reloadFutures[reqID]
	delete(r.reloadFutures, reqID)
	return ch
}

func (r *ServerRuntime) RegisterDeregisterFuture(reqID string, ch chan ackResult) {
	r.mu.Lock()
	r.deregisterFutures[reqID] = ch
	r.mu.Unlock()
}

func (r *ServerRuntime) PopDeregisterFuture(reqID string) chan ackResult {
	r.mu.Lock()
	defer r.mu.Unlock()
	ch := r.deregisterFutures[reqID]
	delete(r.deregisterFutures, reqID)
	return ch
}

func (r *ServerRuntime) SetDeregisterResult(reqID string, f RoutineFactory) {
	r.mu.Lock()
	r.deregisterResults[reqID] = f
	r.mu.Unlock()
}

func (r *ServerRuntime) PopDeregisterResult(reqID string) RoutineFactory {
	r.mu.Lock()
	defer r.mu.Unlock()
	f := r.deregisterResults[reqID]
	delete(r.deregisterResults, reqID)
	return f
}

// --- handle 表 ---

func (r *ServerRuntime) RegisterHandle(childID string, h *RoutineHandle) {
	r.mu.Lock()
	r.handles[childID] = h
	r.mu.Unlock()
}

func (r *ServerRuntime) PopHandle(childID string) *RoutineHandle {
	r.mu.Lock()
	defer r.mu.Unlock()
	h := r.handles[childID]
	delete(r.handles, childID)
	return h
}

func (r *ServerRuntime) GetHandle(childID string) *RoutineHandle {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.handles[childID]
}

// --- req 回执 future 表 ---

func (r *ServerRuntime) RegisterReqFuture(reqID string, ch chan reqReply) {
	r.mu.Lock()
	r.reqFutures[reqID] = ch
	r.mu.Unlock()
}

func (r *ServerRuntime) PopReqFuture(reqID string) chan reqReply {
	r.mu.Lock()
	defer r.mu.Unlock()
	ch := r.reqFutures[reqID]
	delete(r.reqFutures, reqID)
	return ch
}

// --- stream reader 表 ---

func (r *ServerRuntime) RegisterStreamReader(streamID string, reader *StreamReader) {
	r.mu.Lock()
	r.streamReaders[streamID] = reader
	r.mu.Unlock()
}

func (r *ServerRuntime) PopStreamReader(streamID string) *StreamReader {
	r.mu.Lock()
	defer r.mu.Unlock()
	r2 := r.streamReaders[streamID]
	delete(r.streamReaders, streamID)
	return r2
}

func (r *ServerRuntime) GetStreamReader(streamID string) *StreamReader {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.streamReaders[streamID]
}

// --- pubsub 订阅者表 ---

func (r *ServerRuntime) RegisterSubscriber(subscriberID, namespace, topic string, handler SubscribeHandler) {
	r.mu.Lock()
	key := namespace + "\x00" + topic
	if r.subscribers[subscriberID] == nil {
		r.subscribers[subscriberID] = make(map[string]SubscribeHandler)
	}
	r.subscribers[subscriberID][key] = handler
	r.mu.Unlock()
}

func (r *ServerRuntime) GetSubscriberHandler(subscriberID, namespace, topic string) SubscribeHandler {
	r.mu.Lock()
	defer r.mu.Unlock()
	key := namespace + "\x00" + topic
	if subs := r.subscribers[subscriberID]; subs != nil {
		return subs[key]
	}
	return nil
}

func (r *ServerRuntime) PopSubscriber(subscriberID string) {
	r.mu.Lock()
	delete(r.subscribers, subscriberID)
	r.mu.Unlock()
}

func (r *ServerRuntime) PopSubscriberTopic(subscriberID, namespace, topic string) {
	r.mu.Lock()
	key := namespace + "\x00" + topic
	if subs := r.subscribers[subscriberID]; subs != nil {
		delete(subs, key)
		if len(subs) == 0 {
			delete(r.subscribers, subscriberID)
		}
	}
	r.mu.Unlock()
}
