package routine

import (
	"fmt"
	"sync"
	"sync/atomic"
)

// Routine 是 routine 基类接口.子类实现 Run(业务入口)+ 可选 lifecycle hooks.
// 对标 Python Routine ABC:Go 用接口替代继承,BaseRoutine 提供默认实现.
//
// 业务入口方法名是 Run(对齐 Python async def run(self, kwargs)),不是 Start.
// Start 是 lifecycle 概念(kernel 管 create/start/stop/destroy 状态机).
type Routine interface {
	// Name routine 命令名(注册 key,kernel 路由用)
	Name() string
	// Meta 自由扩展元数据(tool/readonly/input_schema/description 等)
	Meta() map[string]any
	// IsPassive 是否被动 routine(kernel 自动 start,不允许业务侧手动 submit)
	IsPassive() bool
	// Run 业务入口(跑到完成或 yield body)
	Run(ctx *RunContext, kwargs map[string]any) (any, error)
}

// LifecycleHooks 可选 lifecycle 回调.routine 实现此接口获得 lifecycle 通知.
type LifecycleHooks interface {
	// Stop 正规 stop 流程;routine 在此 set flag 让 Run 退出.可返回结果.
	Stop() (any, error)
	// OnCreated created 钩子(早于 start).返回声明的 modules.
	OnCreated(rid string, kwargs map[string]any) ([]string, error)
	// OnStarted started 钩子(lifecycle.started 已回报,Run 之前).
	OnStarted() error
	// OnStopped run 完成或退出后调用.reason: auto/stop/error/cancel/force/disconnect.
	OnStopped(reason string, result any, detail string) error
}

// MessageHandler 可选:处理 message.* 定向消息.
type MessageHandler interface {
	OnMessage(source RoutineSource, data any) error
}

// RequestHandler @request handler 签名.
type RequestHandler func(source RoutineSource, data map[string]any) (any, error)

// StreamHandler @stream handler 签名(写 chunk 到 w,结束 Close).
type StreamHandler func(source RoutineSource, data map[string]any, w *StreamWriter) error

// SubscribeHandler @subscribe handler 签名.
type SubscribeHandler func(source RoutineSource, data any) error

// HandlerRegistrar 可选:注册 @request/@stream/@subscribe handler.
// 在 OnCreated 中调用 ctx.HandleRequest/HandleStream/HandleSubscribe 注册.
type HandlerRegistrar interface {
	// RegisterHandlers 在 created 时调,注册 p2p/pubsub handler.
	// ctx 已绑好,handler 可通过闭包捕获 ctx.
	RegisterHandlers(ctx *RunContext)
}

// RoutineSource 发送方 routine 引用.
type RoutineSource struct {
	ID   string
	Name string
}

// BaseRoutine 提供 Routine 接口的默认实现.嵌入后只需实现 Name() 和 Run().
//
//	type Echo struct {
//	    BaseRoutine
//	}
//	func (e *Echo) Name() string { return "echo" }
//	func (e *Echo) Run(ctx *RunContext, kwargs map[string]any) (any, error) { ... }
type BaseRoutine struct{}

func (b *BaseRoutine) Meta() map[string]any          { return map[string]any{} }
func (b *BaseRoutine) IsPassive() bool                { return false }
func (b *BaseRoutine) Stop() (any, error)             { return nil, nil }
func (b *BaseRoutine) OnCreated(rid string, kwargs map[string]any) ([]string, error) {
	return nil, nil
}
func (b *BaseRoutine) OnStarted() error                                  { return nil }
func (b *BaseRoutine) OnStopped(reason string, result any, detail string) error { return nil }
func (b *BaseRoutine) OnMessage(source RoutineSource, data any) error    { return nil }

// RoutineFactory 创建 routine 实例的工厂.对标 Python Routines 存 class.
// 注册时存 factory(不存实例),lifecycle.created 时 Create() 新建实例.
type RoutineFactory interface {
	Name() string
	Create() Routine
	IsPassive() bool
	Meta() map[string]any
}

// SimpleFactory 包装构造函数,实现 RoutineFactory.
type SimpleFactory struct {
	name    string
	create  func() Routine
	passive bool
	meta    map[string]any
}

// NewFactory 创建 factory.name 是 routine 命令名,create 是构造函数.
func NewFactory(name string, create func() Routine) *SimpleFactory {
	return &SimpleFactory{name: name, create: create}
}

// NewFactoryWithMeta 创建 factory(带 meta + passive).
func NewFactoryWithMeta(name string, create func() Routine, passive bool, meta map[string]any) *SimpleFactory {
	return &SimpleFactory{name: name, create: create, passive: passive, meta: meta}
}

func (f *SimpleFactory) Name() string         { return f.name }
func (f *SimpleFactory) Create() Routine       { return f.create() }
func (f *SimpleFactory) IsPassive() bool       { return f.passive }
func (f *SimpleFactory) Meta() map[string]any  { return f.meta }

// Registry routine 注册表:存 factory(不存实例),start 时按需实例化.
// 对标 Python Routines.
type Registry struct {
	mu       sync.RWMutex
	routines map[string]RoutineFactory
}

// NewRegistry 创建空注册表.
func NewRegistry() *Registry {
	return &Registry{routines: make(map[string]RoutineFactory)}
}

// Register 注册 routine factory.同名覆盖.
func (r *Registry) Register(factory RoutineFactory) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.routines[factory.Name()] = factory
}

// RegisterFunc 快捷注册:name + 构造函数.
func (r *Registry) RegisterFunc(name string, create func() Routine) {
	r.Register(NewFactory(name, create))
}

// Get 获取 routine factory by name.
func (r *Registry) Get(name string) RoutineFactory {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.routines[name]
}

// All 返回所有已注册 factory.
func (r *Registry) All() []RoutineFactory {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]RoutineFactory, 0, len(r.routines))
	for _, f := range r.routines {
		out = append(out, f)
	}
	return out
}

// Names 返回所有 routine name 列表.
func (r *Registry) Names() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]string, 0, len(r.routines))
	for name := range r.routines {
		out = append(out, name)
	}
	return out
}

// Deregister 移除已注册的 factory.返回被移除的 factory.
func (r *Registry) Deregister(name string) RoutineFactory {
	r.mu.Lock()
	defer r.mu.Unlock()
	f := r.routines[name]
	delete(r.routines, name)
	return f
}

// Count 返回已注册 routine 数.
func (r *Registry) Count() int {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return len(r.routines)
}

func (r *Registry) String() string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return fmt.Sprintf("Registry(%d routines)", len(r.routines))
}

// reqIDCounter 进程级 req_id 计数器.
var reqIDCounter uint64

// NewReqID 生成唯一 req_id.
func NewReqID() string {
	n := atomic.AddUint64(&reqIDCounter, 1)
	return fmt.Sprintf("r%d", n)
}

// catalogReqIDCounter catalog register/reload/deregister req_id 计数器(前缀 'cat').
var catalogReqIDCounter uint64

// NewCatalogReqID 生成 catalog 操作 req_id.
func NewCatalogReqID() string {
	n := atomic.AddUint64(&catalogReqIDCounter, 1)
	return fmt.Sprintf("cat%d", n)
}
