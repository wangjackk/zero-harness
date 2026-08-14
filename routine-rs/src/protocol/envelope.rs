//! envelope 保留字段名(放进 message.* 的 data 里,对 kernel 透明)。
//!
//! 对齐 Python `protocol.py` 的 `ENVELOPE_*` 常量。req/streamreq 的 envelope
//! 全在 `message.*` 的 data 字段里,kernel 不解析,消费方按这些 key demux。

pub const ENVELOPE_REQ_ID: &str = "__req_id__";
pub const ENVELOPE_REPLY_TO: &str = "__reply_to__";
pub const ENVELOPE_STREAM_ID: &str = "__stream_id__";
/// 业务事件名(@request/@stream 的 key)
pub const ENVELOPE_EVENT: &str = "event";
/// 业务 payload
pub const ENVELOPE_DATA: &str = "data";
pub const ENVELOPE_OK: &str = "ok";
pub const ENVELOPE_ERROR: &str = "error";
pub const ENVELOPE_CHUNK: &str = "chunk";
/// 值:done / error / cancelled
pub const ENVELOPE_EOF: &str = "__eof__";
pub const ENVELOPE_CANCEL: &str = "__cancel__";
