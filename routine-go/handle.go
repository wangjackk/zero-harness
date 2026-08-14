package routine

import (
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
)

// RoutineHandle 父侧拿到的"指向一次 submit 的子 routine"的本地句柄.
// 对标 Python RoutineHandle.
//
// handle.ID = kernel 分配的子 command id(string).notify 路径统一经 kernel 中转.
type RoutineHandle struct {
	ID      string
	Name    string
	Modules []string

	ctx     *RunContext
	result  any
	errMsg  string
	reason  string

	startedMu sync.Mutex
	startedCh chan struct{}
	doneCh    chan struct{}
	doneOnce  sync.Once

	ackMu sync.Mutex
	ack   chan ackResult // start/stop/unsubmit 回执

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
func (h *RoutineHandle) Start() error {
	return h.startCommon(false, false)
}

// TryStart 让 kernel start 子命令,失败时保留可重试(返回 StartError,nil=成功).
func (h *RoutineHandle) TryStart() error {
	return h.startCommon(true, false)
}

// ForceStart 抢占式 start:kernel 驱逐占住子 declared 模块的第三方后 start.
func (h *RoutineHandle) ForceStart() error {
	return h.startCommon(false, true)
}

func (h *RoutineHandle) startCommon(tryMode, force bool) error {
	if h.ctx == nil {
		return errors.New(h.String() + ": no ctx bound")
	}
	if !h.ctx.state.isStarted() {
		return errors.New(h.ctx.Name + ": must AckStart() before starting child")
	}

	var sendErr error
	if force {
		sendErr = h.ctx.io.SendRoutineForceStart(h.ID, h.ctx.PeerID)
	} else {
		sendErr = h.ctx.io.SendRoutineStart(h.ID, tryMode, h.ctx.PeerID)
	}
	if sendErr != nil {
		return sendErr
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
		return nil
	case <-h.doneCh:
		// 子已完成(可能 start 失败后 stopped 到达)
		if h.errMsg != "" {
			return fmt.Errorf("%w: %s", StartError, h.errMsg)
		}
		return nil
	}
}

// Stop 让 kernel stop 子命令(级联).fire=true 时不等 ack.
func (h *RoutineHandle) Stop(fire bool) error {
	if h.ctx == nil {
		return errors.New(h.String() + ": no ctx bound")
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
	}
}

// Unsubmit 撤销提交:清 created 态子命令(未 start 的).
func (h *RoutineHandle) Unsubmit(fire bool) error {
	if h.ctx == nil {
		return errors.New(h.String() + ": no ctx bound")
	}
	if fire {
		return h.ctx.io.SendRoutineUnsubmit(h.ID, h.ctx.PeerID)
	}
	if err := h.ctx.io.SendRoutineUnsubmit(h.ID, h.ctx.PeerID); err != nil {
		return err
	}
	ack := h.newAck()
	select {
	case <-ack:
		return nil
	case <-h.doneCh:
		return nil
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

// RejectAck 收到 rejected(op=start/stop/unsubmit)时调.
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
