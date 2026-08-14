package routine

import (
	"fmt"
	"os"
	"runtime"
	"sync"
	"time"

	"go.uber.org/zap/zapcore"
)

// Logger 结构化日志器,对标 kernel/logger + Python routine/logger.
// 基于 zapcore.Core 自定义输出,格式:
//
//	2025-10-27 15:16:28.080 INFO Publisher - message file.go:42
//
// 整行按级别着色,调用者文件名灰色.单例 GetLogger + Named 派生.

const (
	colorReset  = "\033[0m"
	colorRed    = "\033[31m"
	colorYellow = "\033[33m"
	colorGreen  = "\033[32m"
	colorCyan   = "\033[36m"
	colorGray   = "\033[90m"
)

// Logger 命名日志器.底层共享一个 zapcore.Core(单例).
type Logger struct {
	name string
	core *logCore
}

var (
	loggerOnce sync.Once
	loggerInst *Logger
)

// GetLogger 返回单例 logger.
func GetLogger() *Logger {
	loggerOnce.Do(func() {
		loggerInst = &Logger{name: "", core: &logCore{}}
	})
	return loggerInst
}

// Named 派生命名 logger(如 "RoutineHub","[ROUTINE] echo").
func (l *Logger) Named(name string) *Logger {
	return &Logger{name: name, core: l.core}
}

func (l *Logger) Info(args ...any)  { l.log(zapcore.InfoLevel, fmt.Sprint(args...)) }
func (l *Logger) Infof(t string, a ...any)  { l.log(zapcore.InfoLevel, fmt.Sprintf(t, a...)) }
func (l *Logger) Debug(args ...any) { l.log(zapcore.DebugLevel, fmt.Sprint(args...)) }
func (l *Logger) Debugf(t string, a ...any) { l.log(zapcore.DebugLevel, fmt.Sprintf(t, a...)) }
func (l *Logger) Warn(args ...any)  { l.log(zapcore.WarnLevel, fmt.Sprint(args...)) }
func (l *Logger) Warnf(t string, a ...any)  { l.log(zapcore.WarnLevel, fmt.Sprintf(t, a...)) }
func (l *Logger) Error(args ...any) { l.log(zapcore.ErrorLevel, fmt.Sprint(args...)) }
func (l *Logger) Errorf(t string, a ...any) { l.log(zapcore.ErrorLevel, fmt.Sprintf(t, a...)) }

func (l *Logger) log(level zapcore.Level, msg string) {
	name := l.name
	if name == "" {
		name = "Logger"
	}
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

type logCore struct {
	mu sync.Mutex
}

func (c *logCore) Enabled(zapcore.Level) bool { return true }
func (c *logCore) With([]zapcore.Field) zapcore.Core { return c }

func (c *logCore) Check(entry zapcore.Entry, ce *zapcore.CheckedEntry) *zapcore.CheckedEntry {
	if c.Enabled(entry.Level) {
		return ce.AddCore(entry, c)
	}
	return ce
}

func (c *logCore) Write(entry zapcore.Entry, _ []zapcore.Field) error {
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

func (c *logCore) Sync() error { return os.Stdout.Sync() }
