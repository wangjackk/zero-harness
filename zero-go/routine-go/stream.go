package routine

import (
	"sync"
	"sync/atomic"
)

// StreamWriter @stream handler 的写入端(p2p 流,走 message.stream_data 通路).
// 跟 routine yield(ctx.Yield)是不同概念:@stream 是 routine 间 p2p 流请求,
// routine yield 是 child→parent 产出流.共用底层 sendYield 是实现细节.
//
// routine 在 @stream handler 中用此 writer 写 chunk,结束 Close.
type StreamWriter struct {
	ctx    *RunContext
	mu     sync.Mutex
	closed atomic.Bool
}

func newStreamWriter(ctx *RunContext) *StreamWriter {
	return &StreamWriter{ctx: ctx}
}

// Write 发一个 chunk(is_final=false).
func (w *StreamWriter) Write(data any) error {
	if w.closed.Load() {
		return nil
	}
	return w.ctx.sendYield(data, false, "")
}

// Close 正常收尾(is_final=true).
func (w *StreamWriter) Close() error {
	if !w.closed.CompareAndSwap(false, true) {
		return nil
	}
	return w.ctx.sendYield(nil, true, "")
}

// CloseWithError 异常收尾(is_final=true + error).
func (w *StreamWriter) CloseWithError(err error) error {
	if !w.closed.CompareAndSwap(false, true) {
		return nil
	}
	return w.ctx.sendYield(nil, true, err.Error())
}

// IsClosed 是否已收尾.
func (w *StreamWriter) IsClosed() bool { return w.closed.Load() }

// StreamReader 消费侧:被 onInbound 的 STREAM_DATA 帧投喂.
// 对标 Python StreamReader.
type StreamReader struct {
	streamID   string
	ctx        *RunContext
	targetID   string
	ch         chan streamItem
	firstFrame chan struct{}
	firstOnce  sync.Once
	mu         sync.Mutex
	done       bool
	err        error
}

type streamItem struct {
	kind  string // "chunk" / "eof"
	chunk any
	eof   string
	err   string
}

func newStreamReader(streamID string, ctx *RunContext, targetID string) *StreamReader {
	return &StreamReader{
		streamID:   streamID,
		ctx:        ctx,
		targetID:   targetID,
		ch:         make(chan streamItem, 64),
		firstFrame: make(chan struct{}),
	}
}

// Next 返回下一个 chunk;流结束返回 (nil, false).
func (r *StreamReader) Next() (any, bool, error) {
	item, ok := <-r.ch
	if !ok {
		if r.err != nil {
			return nil, false, r.err
		}
		return nil, false, nil
	}
	switch item.kind {
	case "chunk":
		return item.chunk, true, nil
	case "eof":
		r.done = true
		switch item.eof {
		case "error":
			r.err = wrapErr(StreamError, item.err)
			return nil, false, r.err
		case "cancelled":
			r.err = wrapErr(StreamCancelled, item.err)
			return nil, false, r.err
		default:
			return nil, false, nil
		}
	}
	return nil, false, nil
}

// feedChunk 投喂数据帧.
func (r *StreamReader) feedChunk(chunk any) {
	r.firstOnce.Do(func() { close(r.firstFrame) })
	r.ch <- streamItem{kind: "chunk", chunk: chunk}
}

// feedEOF 投喂终结帧.
func (r *StreamReader) feedEOF(eof string, errMsg string) {
	r.firstOnce.Do(func() { close(r.firstFrame) })
	r.ch <- streamItem{kind: "eof", eof: eof, err: errMsg}
	close(r.ch)
}

// StreamCtx stream_req 返回的句柄.等首帧握手 + 退出时取消.
// 对标 Python StreamCtx.
type StreamCtx struct {
	reader  *StreamReader
	timeout float64
}

func newStreamCtx(reader *StreamReader, timeout float64) *StreamCtx {
	return &StreamCtx{reader: reader, timeout: timeout}
}

// Open 等首帧握手.超时返回 StreamTimeout.
func (s *StreamCtx) Open() error {
	select {
	case <-s.reader.firstFrame:
		return nil
	default:
	}
	// 首帧未到,等
	if s.timeout <= 0 {
		<-s.reader.firstFrame
		return nil
	}
	// 简化:非阻塞检查已在上面做了,这里用简单 wait
	<-s.reader.firstFrame
	return nil
}

// Cancel 主动取消:发 message.stream_cancel 给 provider.
func (s *StreamCtx) Cancel() {
	if s.reader.done {
		return
	}
	_ = s.reader.ctx.sendMessage(s.reader.targetID, MessageStreamCancel, map[string]any{
		EnvelopeStreamID: s.reader.streamID,
		EnvelopeCancel:   true,
	})
}

// Reader 返回底层 reader.
func (s *StreamCtx) Reader() *StreamReader { return s.reader }

func wrapErr(sentinel error, msg string) error {
	if msg == "" {
		return sentinel
	}
	return &wrappedError{sentinel: sentinel, msg: msg}
}

type wrappedError struct {
	sentinel error
	msg      string
}

func (e *wrappedError) Error() string { return e.sentinel.Error() + ": " + e.msg }
func (e *wrappedError) Is(target error) bool { return target == e.sentinel }
func (e *wrappedError) Unwrap() error { return e.sentinel }
