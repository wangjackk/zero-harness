use std::{
    env,
    error::Error,
    fmt,
    io::{self, IsTerminal},
    path::Path,
};

use time::{format_description::FormatItem, macros::format_description, OffsetDateTime};
use tracing::{
    field::{Field, Visit},
    Event, Level, Subscriber,
};
use tracing_subscriber::{
    fmt::{format::Writer, FmtContext, FormatEvent, FormatFields},
    prelude::*,
    registry::LookupSpan,
};

// Re-export：应用层（zero）从 config.toml 读日志级别时无需单独依赖 tracing-subscriber。
pub use tracing_subscriber::filter::LevelFilter;

static LOG_TIME_FORMAT: &[FormatItem<'static>] =
    format_description!("[year]-[month]-[day] [hour]:[minute]:[second].[subsecond digits:3]");

/// 初始化全局 tracing subscriber。
///
/// `level` 为应用层从配置读取的日志级别（如 config.toml 的 `[log].level`），
/// 仅作用于本应用代码（`zero` / `routine`）。底层库（h2/hyper/tonic/rustls/
/// tokio_tungstenite 等）固定压到 `warn`，避免 debug 时底层 HTTP/2 帧日志刷屏。
pub fn init_logging(level: LevelFilter) {
    // per-target 过滤：应用层按用户级别，底层库固定 warn。
    // 不用全局 LevelFilter——那会把 h2/hyper 的 debug 也放出来淹没业务日志。
    let app = level_filter_str(level);
    let filter_str = format!(
        "{app},\
         zero={app},\
         routine={app},\
         h2=warn,hyper=warn,tonic=warn,rustls=warn,\
         tokio_tungstenite=warn,tungstenite=warn,\
         mio=warn,want=warn,hyper_util=warn,\
         hickory_resolver=warn,hickory_proto=warn"
    );
    let env_filter = tracing_subscriber::EnvFilter::try_new(filter_str)
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new(format!("zero={app}")));

    let subscriber = tracing_subscriber::registry().with(env_filter).with(
        tracing_subscriber::fmt::layer()
            .event_format(RoutineLogFormat {
                color_enabled: color_enabled(),
            })
            .fmt_fields(tracing_subscriber::fmt::format::DefaultFields::new())
            .with_writer(std::io::stdout),
    );

    let _ = tracing::subscriber::set_global_default(subscriber);
}

struct RoutineLogFormat {
    color_enabled: bool,
}

impl<S, N> FormatEvent<S, N> for RoutineLogFormat
where
    S: Subscriber + for<'a> LookupSpan<'a>,
    N: for<'a> FormatFields<'a> + 'static,
{
    fn format_event(
        &self,
        _ctx: &FmtContext<'_, S, N>,
        mut writer: Writer<'_>,
        event: &Event<'_>,
    ) -> fmt::Result {
        let metadata = event.metadata();
        if self.color_enabled {
            write!(writer, "\x1b[{}m", level_color(metadata.level()))?;
        }
        write!(
            writer,
            "{} {} {} - ",
            now_text(),
            level_text(metadata.level()),
            logger_name(metadata.target()),
        )?;
        let mut fields = RoutineEventFields::default();
        event.record(&mut fields);
        fields.write_to(writer.by_ref())?;
        if self.color_enabled {
            write!(writer, "\x1b[0m")?;
        }
        if let (Some(file), Some(line)) = (metadata.file(), metadata.line()) {
            if self.color_enabled {
                write!(writer, " \x1b[37;2m")?;
            } else {
                write!(writer, " ")?;
            }
            write!(writer, "{}:{line}", short_file(file))?;
            if self.color_enabled {
                write!(writer, "\x1b[0m")?;
            }
        }
        writeln!(writer)
    }
}

#[derive(Default)]
struct RoutineEventFields {
    message: Option<String>,
    fields: Vec<String>,
}

impl RoutineEventFields {
    fn write_to(&self, mut writer: Writer<'_>) -> fmt::Result {
        for (index, field) in self.fields.iter().enumerate() {
            if index > 0 {
                write!(writer, " ")?;
            }
            write!(writer, "{field}")?;
        }

        if let Some(message) = &self.message {
            if !self.fields.is_empty() {
                write!(writer, " ")?;
            }
            write!(writer, "{message}")?;
        }

        Ok(())
    }

    fn record_value(&mut self, field: &Field, value: String) {
        if field.name() == "message" {
            self.message = Some(value);
        } else {
            self.fields.push(format!("{}={value}", field.name()));
        }
    }
}

impl Visit for RoutineEventFields {
    fn record_debug(&mut self, field: &Field, value: &dyn fmt::Debug) {
        self.record_value(field, format!("{value:?}"));
    }

    fn record_str(&mut self, field: &Field, value: &str) {
        self.record_value(field, value.to_string());
    }

    fn record_i64(&mut self, field: &Field, value: i64) {
        self.record_value(field, value.to_string());
    }

    fn record_u64(&mut self, field: &Field, value: u64) {
        self.record_value(field, value.to_string());
    }

    fn record_bool(&mut self, field: &Field, value: bool) {
        self.record_value(field, value.to_string());
    }

    fn record_error(&mut self, field: &Field, value: &(dyn Error + 'static)) {
        self.record_value(field, value.to_string());
    }
}

fn now_text() -> String {
    OffsetDateTime::now_local()
        .unwrap_or_else(|_| OffsetDateTime::now_utc())
        .format(LOG_TIME_FORMAT)
        .unwrap_or_else(|_| String::from("0000-00-00 00:00:00.000"))
}

/// `LevelFilter` → EnvFilter 用的小写字符串（`info`/`debug`/...）。
/// `OFF` 映射为 `error`（EnvFilter 无 off 段，error 已是最低噪声）。
fn level_filter_str(level: LevelFilter) -> &'static str {
    match level {
        LevelFilter::OFF | LevelFilter::ERROR => "error",
        LevelFilter::WARN => "warn",
        LevelFilter::INFO => "info",
        LevelFilter::DEBUG => "debug",
        LevelFilter::TRACE => "trace",
    }
}

fn level_text(level: &Level) -> &'static str {
    match *level {
        Level::ERROR => "ERROR",
        Level::WARN => "WARNING",
        Level::INFO => "INFO",
        Level::DEBUG => "DEBUG",
        Level::TRACE => "TRACE",
    }
}

fn color_enabled() -> bool {
    match env::var("ROUTINE_LOG_COLOR").as_deref() {
        Ok("1") => true,
        Ok("0") => false,
        _ => io::stdout().is_terminal(),
    }
}

fn level_color(level: &Level) -> &'static str {
    match *level {
        Level::ERROR => "31",
        Level::WARN => "33",
        Level::INFO => "32",
        Level::DEBUG | Level::TRACE => "36",
    }
}

fn logger_name(target: &str) -> &str {
    target.rsplit("::").next().unwrap_or(target)
}

fn short_file(file: &str) -> &str {
    Path::new(file)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(file)
}
