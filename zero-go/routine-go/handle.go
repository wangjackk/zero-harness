package routine

import (
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"time"
)

// ackTimeout 是 start/stop/destroy 等 kernel 回执的默认超时(对标 Python
// ctx.ACK_TIMEOUT): 回执丢失/路由遗漏时报错返回, 不永久阻塞.
const ackTimeout = 30 * time.Second

// RoutineHandle 父侧拿到的"指向一次 create 的子 routine"的本地句柄.
// 对标 Python RoutineHandle.
//
// handle.ID = kernel 分配的子 command id(string).notify 路径统一经 kernel 中转.
type RoutineHandle struct {
	ID      string
	Name    string
	Modules []string
	// Priority start 成功后镜像的档位(kernel Node.priority 的调用方视图).
	// nil=尚未 start.编排器可读(h1.Priority vs h2.Priority 辅助裁决).
	Priority *int

	ctx     *RunContext
	result  any
	errMsg  string
	reason  string

	startedMu sync.Mutex
	startedCh chan struct{}
	doneCh    chan struct{}
	doneOnce  sync.Once

	ackMu sync.Mutex
	ack   chan ackResult // start/stop/destroy 回执

	// body 迭代
	bodyCh    chan any
	bodyDone  atomic.Bool

	// 生命周期回调
	OnStartedHandler func(h *RoutineHandle)
	OnStoppedHandler func(h *RoutineHandle)
}

func newRoutineHandle(childID, name string, ctx *RunContext, modules []string) *RoutineHandle {
	return &RoutineHandle{
		ID:       childID,
		Name:     name,
		Modules:  modules,
		ctx:      ctx,
		startedCh: make(chan struct{}),
		doneCh:    make(chan struct{}),
		ack:       nil,
		bodyCh:    make(chan any, 64),
	}
}

// IsStarted 是否已 started.
func (h *RoutineHandle) IsStarted() bool {
	select {
	case <-h.startedCh:
		return true
	default:
		return false
	}
}

// IsDone 是否已完成.
func (h *RoutineHandle) IsDone() bool {
	select {
	case <-h.doneCh:
		return true
	default:
		return false
	}
}

// --- control ---

// Start 让 kernel start 子命令.失败 raise StartError.
//
// priority(模块冲突时的抢占裁决):incoming 严格大于全部第三方 holder 才逐个驱逐
// (cascade stop,reason='force' 透传)后 start;存在 >= 平级 holder 时不驱逐任何
// 节点,原样拒(平级不抢).默认 10=常规默认档;0 为最低档,永无驱逐资格.
// 优先级只裁第三方(永不驱逐自己/祖先).
func (h *RoutineHandle) Start(priority int) error {
	return h.startCommon(false, priority)
}

// TryStart 让 kernel start 子命令,失败时保留可重试(返回 StartError,nil=成功).
// priority=0 最低档: try_start 是礼貌语义, 永不抢占.要抢占走 Start(priority).
func (h *RoutineHandle) TryStart() error {
	return h.startCommon(true, 0)
}

// startCommon start/try_start 公共骨架.成功后镜像 priority 到 h.Priority
// (kernel Node.priority 的调用方视图).
func (h *RoutineHandle) startCommon(tryMode bool, priority int) error {
	if h.ctx == nil {
		return errors.New(h.String() + ": no ctx bound")
	}
	if err := h.requireNotDone("start"); err != nil {
		return err
	}
	if !h.ctx.state.isStarted() {
		return errors.New(h.ctx.Name + ": must AckStart() before starting child")
	}

	if err := h.ctx.io.SendRoutineStart(h.ID, tryMode, priority, h.ctx.PeerID); err != nil {
		return err
	}

	ack := h.newAck()
	select {
	case res := <-ack:
		if !res.ok {
			err := fmt.Errorf("%w: %s", StartError, res.err)
			if tryMode {
				return err
			}
			return err
		}
		p := priority
		h.Priority = &p
		return nil
	case <-h.doneCh:
		// 子已完成(可能 start 失败后 stopped 到达)
		if h.errMsg != "" {
			return fmt.Errorf("%w: %s", StartError, h.errMsg)
		}
		p := priority
		h.Priority = &p
		return nil
	case <-time.After(ackTimeout):
		return fmt.Errorf("%s: start ack timeout after %s", h, ackTimeout)
	}
}

// requireNotDone 已 done 的 handle 不接受控制命令: kernel 侧 node 已删,
// 回执(rejected)无人认领 ---- 只能靠 ack 超时兜底. 入口即报错.
func (h *RoutineHandle) requireNotDone(op string) error {
	if h.IsDone() {
		return fmt.Errorf("%s: already done (reason=%s), cannot %s", h, h.reason, op)
	}
	return nil
}

// Stop 让 kernel stop 子命令(级联).fire=true 时不等 ack.
func (h *RoutineHandle) Stop(fire bool) error {
	if h.ctx == nil {
		return errors.New(h.String() + ": no ctx bound")
	}
	if err := h.requireNotDone("stop"); err != nil {
		return err
	}
	if fire {
		return h.ctx.io.SendRoutineStop(h.ID, h.ctx.PeerID)
	}
	if !h.ctx.state.isStarted() {
		return errors.New(h.ctx.Name + ": must AckStart() before stopping child")
	}
	if err := h.ctx.io.SendRoutineStop(h.ID, h.ctx.PeerID); err != nil {
		return err
	}
	ack := h.newAck()
	select {
	case <-ack:
		return nil
	case <-h.doneCh:
		return nil
	case <-time.After(ackTimeout):
		return fmt.Errorf("%s: stop ack timeout after %s", h, ackTimeout)
	}
}

// Destroy 撤销提交:清 created 态子命令(未 start 的).
func (h *RoutineHandle) Destroy(fire bool) error {
	if h.ctx == nil {
		return errors.New(h.String() + ": no ctx bound")
	}
	if err := h.requireNotDone("destroy"); err != nil {
		return err
	}
	if fire {
		return h.ctx.io.SendRoutineDestroy(h.ID, h.ctx.PeerID)
	}
	if err := h.ctx.io.SendRoutineDestroy(h.ID, h.ctx.PeerID); err != nil {
		return err
	}
	ack := h.newAck()
	select {
	case <-ack:
		return nil
	case <-h.doneCh:
		return nil
	case <-time.After(ackTimeout):
		return fmt.Errorf("%s: destroy ack timeout after %s", h, ackTimeout)
	}
}

// --- waits ---

// WaitStarted 等 lifecycle.started.
func (h *RoutineHandle) WaitStarted() {
	<-h.startedCh
}

// Wait 等 lifecycle.stopped.成功返回 result,失败返回 error.
func (h *RoutineHandle) Wait() (any, error) {
	<-h.doneCh
	if h.errMsg != "" {
		return nil, errors.New(h.errMsg)
	}
	return h.result, nil
}

// --- service-layer hooks(server reader 按 child_id 路由调用) ---

// NotifyStarted lifecycle.started 到达.
func (h *RoutineHandle) NotifyStarted() {
	h.startedMu.Lock()
	select {
	case <-h.startedCh:
	default:
		close(h.startedCh)
	}
	h.startedMu.Unlock()
	h.resolveAck(ackResult{ok: true})
	if h.OnStartedHandler != nil {
		h.OnStartedHandler(h)
	}
}

// NotifyDone lifecycle.stopped 到达.
func (h *RoutineHandle) NotifyDone(result any, errMsg string, reason string) {
	h.result = result
	h.errMsg = errMsg
	h.reason = reason
	// 兜底:错过 started 直接 done 时也唤醒 WaitStarted
	h.startedMu.Lock()
	select {
	case <-h.startedCh:
	default:
		close(h.startedCh)
	}
	h.startedMu.Unlock()
	h.doneOnce.Do(func() { close(h.doneCh) })
	h.finishYield()
	h.resolveAck(ackResult{ok: errMsg == ""})
	if h.OnStoppedHandler != nil {
		h.OnStoppedHandler(h)
	}
}

// RejectAck 收到 rejected(op=start/stop/destroy)时调.
func (h *RoutineHandle) RejectAck(err string) {
	h.resolveAck(ackResult{ok: false, err: err})
}

func (h *RoutineHandle) newAck() chan ackResult {
	h.ackMu.Lock()
	defer h.ackMu.Unlock()
	h.ack = make(chan ackResult, 1)
	return h.ack
}

func (h *RoutineHandle) resolveAck(res ackResult) {
	h.ackMu.Lock()
	if h.ack != nil {
		select {
		case h.ack <- res:
		default:
		}
		h.ack = nil
	}
	h.ackMu.Unlock()
}

// --- yield 迭代(子 routine yield 时) ---

// OnYieldChunk routine.yielded 投喂.
func (h *RoutineHandle) OnYieldChunk(data any, isFinal bool, errMsg string) {
	if h.bodyDone.Load() {
		return
	}
	if errMsg != "" {
		h.bodyDone.Store(true)
		h.bodyCh <- fmt.Errorf("%s", errMsg)
		close(h.bodyCh)
		return
	}
	if isFinal {
		h.bodyDone.Store(true)
		close(h.bodyCh)
		return
	}
	h.bodyCh <- data
}

func (h *RoutineHandle) finishYield() {
	if !h.bodyDone.Swap(true) {
		close(h.bodyCh)
	}
}

// YieldChan 返回 yield 迭代 channel(子 routine yield 的项 / close = 终结).
func (h *RoutineHandle) YieldChan() <-chan any {
	return h.bodyCh
}

func (h *RoutineHandle) String() string {
	state := "pending"
	if h.IsDone() {
		if h.errMsg != "" {
			state = "error"
		} else {
			state = "done"
		}
	} else if h.IsStarted() {
		state = "started"
	}
	return fmt.Sprintf("RoutineHandle(%s id=%s %s)", h.Name, h.ID, state)
}
