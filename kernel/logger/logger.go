// Package logger 调度器侧结构化日志器.
//
// 基于 go.uber.org/zap 的 zapcore.Core 自定义输出:
//
//	2025-10-27 15:16:28.080 INFO Publisher - message file.go:42
//
// 整行按级别着色(INFO 绿 / DEBUG 青 / WARN 黄 / ERROR 红),调用者文件名灰色.
// 单例 GetLogger + Named(name) 派生命名 logger(如 "rpc","INIT").
package logger

import (
	"fmt"
	"os"
	"runtime"
	"sync"
	"time"

	"go.uber.org/zap/zapcore"
)

// ANSI 颜色代码.
const (
	colorReset  = "\033[0m"
	colorRed    = "\033[31m" // ERROR
	colorYellow = "\033[33m" // WARN
	colorGreen  = "\033[32m" // INFO
	colorCyan   = "\033[36m" // DEBUG
	colorGray   = "\033[90m" // 文件名(灰色)
)

// Logger 命名日志器.底层共享一个 zapcore.Core(单例),name 区分来源.
type Logger struct {
	name string
	core zapcore.Core
}

var (
	instance *Logger
	once     sync.Once
)

// GetLogger 返回单例 logger(name 为空,输出时显示 "Logger").
func GetLogger() *Logger {
	once.Do(func() {
		instance = &Logger{name: "", core: &core{}}
	})
	return instance
}

// Named 派生一个命名 logger(如 "rpc","INIT","Publisher").
func (l *Logger) Named(name string) *Logger {
	return &Logger{name: name, core: l.core}
}

// Info / Infof / Debug / Debugf / Warn / Warnf / Error / Errorf 八级 API.
func (l *Logger) Info(args ...interface{})  { l.log(zapcore.InfoLevel, fmt.Sprint(args...)) }
func (l *Logger) Infof(t string, a ...any)  { l.log(zapcore.InfoLevel, fmt.Sprintf(t, a...)) }
func (l *Logger) Debug(args ...interface{}) { l.log(zapcore.DebugLevel, fmt.Sprint(args...)) }
func (l *Logger) Debugf(t string, a ...any) { l.log(zapcore.DebugLevel, fmt.Sprintf(t, a...)) }
func (l *Logger) Warn(args ...interface{})  { l.log(zapcore.WarnLevel, fmt.Sprint(args...)) }
func (l *Logger) Warnf(t string, a ...any) { l.log(zapcore.WarnLevel, fmt.Sprintf(t, a...)) }
func (l *Logger) Error(args ...interface{}) { l.log(zapcore.ErrorLevel, fmt.Sprint(args...)) }
func (l *Logger) Errorf(t string, a ...any) { l.log(zapcore.ErrorLevel, fmt.Sprintf(t, a...)) }

// log 内部输出:取调用者(跳过本方法 + Info/Warn/etc 两层),
// 构造 zapcore.Entry 经 core.Write 自定义格式化输出到 stdout.
func (l *Logger) log(level zapcore.Level, msg string) {
	name := l.name
	if name == "" {
		name = "Logger"
	}
	// runtime.Caller(2):跳过 log(0) 和 Info/Infof/etc(1),定位到调用方代码行.
	// 在 log 里取 caller 放进 entry,而不是在 core.Write 里取
	// (zap 的 CheckedEntry.Write 在 core.Write 和 log 之间多一层栈帧,会跳错).
	pc, file, line, ok := runtime.Caller(2)
	var caller zapcore.EntryCaller
	if ok {
		caller = zapcore.NewEntryCaller(pc, file, line, ok)
	}
	entry := zapcore.Entry{
		Level:      level,
		Time:       time.Now(),
		LoggerName: name,
		Message:    msg,
		Caller:     caller,
	}
	if ce := l.core.Check(entry, nil); ce != nil {
		ce.Write()
	}
}

// core 自定义 zapcore.Core:直接 fmt.Fprintf 到 stdout,不走 zap 的 encoder.
type core struct {
	mu sync.Mutex // 序列化输出,避免多 goroutine 交错
}

func (c *core) Enabled(zapcore.Level) bool { return true }

func (c *core) With([]zapcore.Field) zapcore.Core { return c }

func (c *core) Check(entry zapcore.Entry, ce *zapcore.CheckedEntry) *zapcore.CheckedEntry {
	if c.Enabled(entry.Level) {
		return ce.AddCore(entry, c)
	}
	return ce
}

// Write 格式化输出:<color><time> <LEVEL> <name> - <msg><reset> <gray><caller><reset>
// caller 取自 entry.Caller(在 log 里 runtime.Caller(2) 算好的),TrimmedPath 裁后 2 段.
func (c *core) Write(entry zapcore.Entry, _ []zapcore.Field) error {
	var levelColor string
	switch entry.Level {
	case zapcore.DebugLevel:
		levelColor = colorCyan
	case zapcore.InfoLevel:
		levelColor = colorGreen
	case zapcore.WarnLevel:
		levelColor = colorYellow
	case zapcore.ErrorLevel, zapcore.DPanicLevel, zapcore.PanicLevel, zapcore.FatalLevel:
		levelColor = colorRed
	default:
		levelColor = colorReset
	}

	timeStr := entry.Time.Format("2006-01-02 15:04:05.000")
	levelStr := entry.Level.CapitalString()
	caller := entry.Caller.TrimmedPath()

	c.mu.Lock()
	fmt.Fprintf(os.Stdout, "%s%s %s %s - %s%s %s%s%s\n",
		levelColor, timeStr, levelStr, entry.LoggerName, entry.Message, colorReset,
		colorGray, caller, colorReset)
	c.mu.Unlock()
	return nil
}

func (c *core) Sync() error { return os.Stdout.Sync() }
