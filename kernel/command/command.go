package command

import (
	"context"
	"fmt"
	"sync"
	"sync/atomic"
)

// State 命令生命周期状态
type State int

const (
	StateCreated  State = iota // created,已创建未运行
	StateStarting             // 已 Start,已发 lifecycle.start,等 lifecycle.started 回报
	StateRunning              // 已 started(lifecycle.stopped 回报过),运行中
	StateStopping             // 打断中(已发 stop,等 body 退出)
	StateStopped              // 已停止
)

func (s State) String() string {
	switch s {
	case StateCreated:
		return "created"
	case StateStarting:
		return "starting"
	case StateRunning:
		return "running"
	case StateStopping:
		return "stopping"
	case StateStopped:
		return "stopped"
	}
	return "?"
}

// Command kernel 的执行单元.
//
// New/Create 后处于 created 态.Start 进入 starting 态(已发 lifecycle.start,等
// lifecycle.started 回报后才算 running).模块冲突校验+占用由 shell 在 Start 前做,
// 释放由 shell 在 body 退出后做.空 Modules = 不占资源,永不被挡.
type Command struct {
	Name string
	ID   int

	ParentCommandID int
	SubCommandIDs   []int

	// Kwargs 是 routine 入参(submit kwargs 的单一来源):created 阶段经
	// lifecycle.created 投递给 created(),start 阶段经 lifecycle.start 投递给
	// start()----两阶段共用同一份.submit 回环时由 OnSubmitCreated 存入;
	// Execute 直启时由 Create 存入.runRemote 读出经 CreateRoutine/StartRoutine
	// 发给远端.nil = 无入参.
	Kwargs map[string]any
	Modules []string

	// Run 是 routine 体.ctx 被取消即表示被打断,应尽快退出.
	Run func(ctx context.Context, cmd *Command)

	mu     sync.Mutex
	state  State
	cancel context.CancelFunc
	done   chan struct{}
}

var idCounter atomic.Int64

func New(name string, run func(ctx context.Context, cmd *Command)) *Command {
	return &Command{
		Name:  name,
		ID:    int(idCounter.Add(1)),
		Run:   run,
		state: StateCreated,
		done:  make(chan struct{}),
	}
}

func (c *Command) SetModules(modules ...string) { c.Modules = modules }

// AddModules 把 modules 并集进 c.Modules(去重).运行时 acquire 成功后调,
// 让 cmd.Modules 反映 routine 当前占的全部模块(静态声明 + 运行时 acquire).
func (c *Command) AddModules(modules ...string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	have := map[string]struct{}{}
	for _, m := range c.Modules {
		have[m] = struct{}{}
	}
	for _, m := range modules {
		if _, ok := have[m]; !ok {
			c.Modules = append(c.Modules, m)
			have[m] = struct{}{}
		}
	}
}

// RemoveModules 从 c.Modules 移除 modules.运行时 release 成功后调.
func (c *Command) RemoveModules(modules ...string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	drop := map[string]struct{}{}
	for _, m := range modules {
		drop[m] = struct{}{}
	}
	out := c.Modules[:0]
	for _, m := range c.Modules {
		if _, ok := drop[m]; !ok {
			out = append(out, m)
		}
	}
	c.Modules = out
}

func (c *Command) State() State {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.state
}

// ModulesList 返回 cmd.Modules 的拷贝(持锁).
// 运行时 acquire/release 会并发改 Modules(reader goroutine 经 OnAcquire→AddModules),
// 直接读 c.Modules 裸字段有 data race(fmt.Printf %v 反射读也中)----要观测当前模块
// 必须走本方法加锁拷贝.持锁拷贝后释放,调用方拿到的切片不受后续 acquire/release 影响.
func (c *Command) ModulesList() []string {
	c.mu.Lock()
	defer c.mu.Unlock()
	out := make([]string, len(c.Modules))
	copy(out, c.Modules)
	return out
}

// Start 启动命令:进入 starting 态(已发 lifecycle.start,等 lifecycle.started 回报后
// 才算 running).模块冲突校验+占用由 shell 在调本方法前完成.非 created 态调用无效.
//
// 状态流转:created → starting(Start 后)→ running(MarkRunning 后)→ stopped.
// MarkRunning 由 shell 在收到 lifecycle.started 回报后调.
func (c *Command) Start() error {
	c.mu.Lock()
	if c.state != StateCreated {
		c.mu.Unlock()
		return nil
	}

	c.state = StateStarting
	ctx, cancel := context.WithCancel(context.Background())
	c.cancel = cancel
	c.mu.Unlock()

	go func() {
		c.Run(ctx, c)
		c.mu.Lock()
		c.state = StateStopped
		c.mu.Unlock()
		close(c.done)
	}()
	return nil
}

// MarkRunning 标记命令进入 running 态(lifecycle.started 回报后由 shell 调).
// starting 之外的态调用是 no-op(已 running / 已 stopped 等不变).
func (c *Command) MarkRunning() {
	c.mu.Lock()
	if c.state == StateStarting {
		c.state = StateRunning
	}
	c.mu.Unlock()
}

// Stop 打断命令:取消 ctx 并等 body 退出(模块由 body 退出时释放).
// 对未启动 / 已停止的命令调用是 no-op.starting / running 态都可被打断.
func (c *Command) Stop() {
	c.mu.Lock()
	if c.state != StateStarting && c.state != StateRunning {
		c.mu.Unlock()
		return
	}
	c.state = StateStopping
	cancel := c.cancel
	c.mu.Unlock()

	if cancel != nil {
		cancel()
	}
	<-c.done
}

// Wait 阻塞直到命令 body 退出(正常完成或被打断).未 Start / 已停止的命令立即返回.
// 用于需要等一条 routine 自然结束的场景(如 compose submit→start→wait 链).
func (c *Command) Wait() {
	c.mu.Lock()
	state := c.state
	c.mu.Unlock()
	if state == StateCreated || state == StateStopped {
		return
	}
	<-c.done
}

// String 返回 "name-id" 形式.
// 供日志 %s 自动调用----简洁且统一,避免每处手拼 fmt.Sprintf("%s#%d", ...).
func (c *Command) String() string {
	return fmt.Sprintf("%s-%d", c.Name, c.ID)
}
