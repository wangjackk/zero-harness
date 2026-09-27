//! 日志初始化:tracing → stdout,py logging 同构格式。

use std::fmt;
use std::io::IsTerminal;

use tracing::Subscriber;
use tracing_subscriber::{
    fmt::{time::FormatTime, FmtContext, FormatEvent, FormatFields},
    registry::LookupSpan,
};

/// 本地时区时间戳(毫秒): `2026-09-09 02:31:52.609`。
struct LocalMs;

impl FormatTime for LocalMs {
    fn format_time(&self, w: &mut tracing_subscriber::fmt::format::Writer<'_>) -> fmt::Result {
        write!(
            w,
            "{}",
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S%.3f")
        )
    }
}

/// py logging 同构格式(对齐 routine-py `logger.py` 的 ColoredFormatter):
/// `<color>时间 LEVEL target - message<reset> <gray>file:line<reset>`
/// 整行按级别着色(INFO 绿 / DEBUG 青 / WARN 黄 / ERROR 红),caller 灰色;
/// 非 tty(重定向)不发转义码。
struct PyStyleEvent {
    ansi: bool,
}

impl PyStyleEvent {
    /// 整行级别色。对齐 py `_LOG_COLORS`:
    /// DEBUG 青 / INFO 绿 / WARNING 黄 / ERROR 红;TRACE 为 py 没有的级别,补紫。
    fn line_color(level: &tracing::Level) -> &'static str {
        match *level {
            tracing::Level::TRACE => "\x1b[35m",
            tracing::Level::DEBUG => "\x1b[36m",
            tracing::Level::INFO => "\x1b[32m",
            tracing::Level::WARN => "\x1b[33m",
            tracing::Level::ERROR => "\x1b[31m",
        }
    }
}

impl<S, N> FormatEvent<S, N> for PyStyleEvent
where
    S: Subscriber + for<'a> LookupSpan<'a>,
    N: for<'a> tracing_subscriber::fmt::FormatFields<'a> + 'static,
{
    fn format_event(
        &self,
        ctx: &FmtContext<'_, S, N>,
        mut writer: tracing_subscriber::fmt::format::Writer<'_>,
        event: &tracing::Event<'_>,
    ) -> fmt::Result {
        const RESET: &str = "\x1b[0m";
        const GRAY: &str = "\x1b[90m";
        let meta = event.metadata();
        // <color>time LEVEL target - msg
        if self.ansi {
            writer.write_str(Self::line_color(meta.level()))?;
        }
        LocalMs.format_time(&mut writer)?;
        write!(writer, " {} {} - ", meta.level(), meta.target())?;
        ctx.format_fields(writer.by_ref(), event)?;
        // <reset> <gray>file:line<reset>
        if self.ansi {
            writer.write_str(RESET)?;
        }
        if let Some(file) = meta.file() {
            if let Some(line) = meta.line() {
                let name = file.rsplit(['/', '\\']).next().unwrap_or(file);
                if self.ansi {
                    write!(writer, " {GRAY}{name}:{line}{RESET}")?;
                } else {
                    write!(writer, " {name}:{line}")?;
                }
            }
        }
        writeln!(writer)
    }
}

/// 安装全局 tracing 订阅:stdout 输出,py 同构格式,与 banner 同流。
///
/// with_ansi(false): 字段格式(DefaultFields)自带的 italic/dimmed 样式
/// 每段都发 reset, 会掐断 event_format 的整行级别色(裸值变白);
/// ANSI 码全由 PyStyleEvent 自己发, 只开行首/行尾各一次。
pub fn init() {
    tracing_subscriber::fmt()
        .with_ansi(false)
        .with_writer(std::io::stdout)
        .event_format(PyStyleEvent {
            ansi: std::io::stdout().is_terminal(),
        })
        .init();
}
