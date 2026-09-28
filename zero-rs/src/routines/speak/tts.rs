//! 进程级 TTS 合成服务（actor，多实例串行）。
//!
//! 一个常驻 task 独占 **一条常驻** Seed TTS 双向 WebSocket 连接：
//! - WS 连接跨 session 复用（对齐 Python `SeedWSConnection._ensure_connected`）：
//!   每次 speak 只 StartSession，不重连；连接断开/失败时懒重连。省掉每句
//!   speak 的 WebSocket connect + StartConnection 开销（~300ms）。
//! - 每个 speak 实例持一个 [`TtsHandle`]（唯一 token 即实例身份），命令带
//!   token 进入 actor；actor 把命令归到各自实例队列，实例间严格 FIFO 串行：
//!   只有队首实例的命令会执行（建 session / 发文本），其余实例的命令缓存
//!   在各自队列等待。mailbox 会在实例 start 前投递 body，多实例命令天然
//!   交叉到达，token 隔离保证互不串扰（对齐 Python 每实例独立 `SeedTTS`
//!   feed queue + 连接锁排队的语义）。
//! - 实例的命令还要过 started 门（收到 WaitDone = run 已启动）才执行：
//!   合成+播放不早于 shell 调度它的时机（对齐 py `audio_chunks` 在 run
//!   里才消费——上一段 play_music 播放时，下一段 speak 不提前出声）。
//! - 队首实例经 [`feed`](TtsHandle::feed) / [`finish`](TtsHandle::finish)
//!   增量发文本片段（流式）；[`wait_done`](TtsHandle::wait_done) 等合成 +
//!   播放结束。
//! - 服务端返回的 PCM 帧经 [`PcmSink`](crate::audio::service::PcmSink)
//!   喂给 [`AudioService`](crate::audio::service::AudioService)，由后者
//!   独占设备边收边播；`wait_done` 在 PCM 全部喂完后发 `end`，等 audio drain
//!   设备队列播完。
//!
//! TTS 与 audio 经一条 typed channel 解耦：TTS 只持 `PcmSink`（sender 薄
//! 封装）+ done receiver，不 import 任何 rodio 类型、不碰等待逻辑。无锁：
//! 所有 client 操作集中在 actor 单 task 内；routine 侧只持
//! [`TtsHandle`]（token + 发命令，天然 `Send + Sync`）。

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use routine::core::RoutineError;
use tokio::sync::{mpsc, oneshot};
use uuid::Uuid;

use crate::audio::service::{audio, AudioOutcome, PcmSink};

use super::protocol::{Event, Frame, FrameKind};
use super::seed::{SeedClient, SeedConfig};
use super::{CHANNELS, SAMPLE_RATE};

/// 一次合成的结果。
#[derive(Debug, Clone, Default)]
pub struct SpeakOutcome {
    pub frames: usize,
    pub interrupted: bool,
}

/// 给 actor 的命令。所有命令携带实例 token —— actor 靠它把命令路由到
/// 对应实例，是隔离的根基。
enum TtsCmd {
    /// 喂一片文本（增量）。`additions`: 本实例的情绪指令活单元 —— run 起点经
    /// agent/state 现取后写入; session 惰性建立于首个 Feed 的消化(受 started
    /// 门, 晚于 run), 建立时读活单元现值, 不在 Feed 发送时刻拷贝快照.
    Feed {
        token: u64,
        text: String,
        additions: Arc<Mutex<Option<String>>>,
    },
    /// 文本喂完，发 FinishSession 收口。
    Finish { token: u64 },
    /// 打断：cancel session + 停播放。
    Cancel { token: u64 },
    /// 等合成 + 播放结束，回 outcome。
    WaitDone {
        token: u64,
        reply: oneshot::Sender<SpeakOutcome>,
    },
}

impl TtsCmd {
    fn token(&self) -> u64 {
        match self {
            TtsCmd::Feed { token, .. }
            | TtsCmd::Finish { token }
            | TtsCmd::Cancel { token }
            | TtsCmd::WaitDone { token, .. } => *token,
        }
    }
}

/// 实例 token 分配器（进程级单调递增）。
static TOKEN: AtomicU64 = AtomicU64::new(1);

/// 对 TTS 服务的句柄。每个 speak 实例一个（`TtsHandle::new` 分配唯一
/// token），token 即命令路由的实例身份。
///
/// 实质只是 token：命令发送时才从 [`HANDLE`] 取 actor channel，因此
/// handle 可在 actor 启动前构造（factory create 早于 on_created）。
pub struct TtsHandle {
    token: u64,
}

impl Default for TtsHandle {
    fn default() -> Self {
        Self::new()
    }
}

impl TtsHandle {
    pub fn new() -> Self {
        Self {
            token: TOKEN.fetch_add(1, Ordering::Relaxed),
        }
    }

    fn send(&self, make: impl FnOnce(u64) -> TtsCmd) {
        if let Some(tx) = HANDLE.get() {
            let _ = tx.send(make(self.token));
        }
    }

    pub async fn feed(
        &self,
        text: &str,
        additions: Arc<Mutex<Option<String>>>,
    ) -> Result<(), RoutineError> {
        if !text.is_empty() {
            self.send(|token| TtsCmd::Feed {
                token,
                text: text.to_string(),
                additions,
            });
        }
        Ok(())
    }

    pub fn finish(&self) -> Result<(), RoutineError> {
        self.send(|token| TtsCmd::Finish { token });
        Ok(())
    }

    pub fn cancel(&self) {
        self.send(|token| TtsCmd::Cancel { token });
    }

    /// 等合成 + 播放结束。若 actor 已退出或等不到，返回默认 outcome。
    pub async fn wait_done(&self) -> Result<SpeakOutcome, RoutineError> {
        let (tx, rx) = oneshot::channel();
        let sent = HANDLE.get().map(|h| {
            h.send(TtsCmd::WaitDone {
                token: self.token,
                reply: tx,
            })
            .is_ok()
        });
        if sent != Some(true) {
            return Ok(SpeakOutcome::default());
        }
        Ok(rx.await.unwrap_or_default())
    }
}

/// 全局 actor channel 单例。`TtsService::start` 写入，[`TtsHandle::send`] 读取。
static HANDLE: OnceLock<mpsc::UnboundedSender<TtsCmd>> = OnceLock::new();

/// 进程级 TTS 服务：负责启动 actor task。
pub struct TtsService;

impl TtsService {
    /// 启动 actor task 并登记全局 channel。可重复调用，后续忽略。
    pub fn start(config: SeedConfig) -> Result<&'static Self, RoutineError> {
        if HANDLE.get().is_some() {
            return Ok(&Self); // 已启动
        }
        let (tx, rx) = mpsc::unbounded_channel::<TtsCmd>();
        let _ = HANDLE.set(tx);
        tokio::spawn(actor_loop(config, rx));
        Ok(&Self)
    }
}

// ── actor ──────────────────────────────────────────────────────

/// 一次 session 的内部状态（actor 持有，串行访问，无锁）。
///
/// 不含 `SeedClient`——WS 连接提到 actor 级常驻复用（对齐 Python
/// `SeedWSConnection` 跨 session 复用同一条连接），session 只存本次会话的
/// id / sink / 计数等。client 操作经 actor 的 `client` 字段做。
struct Session {
    id: String,
    sink: PcmSink,
    done: oneshot::Receiver<AudioOutcome>,
    frames: usize,
    interrupted: bool,
    finish_sent: bool,
    finished: bool, // 收到 SessionFinished
}

/// Finalizing 态：session 已 drain 完 PCM、发了 End，正在等 audio task 播完。
/// 等待期间仍要响应 Cancel —— 否则 interrupt 时音频继续播完（曾出现的 bug）。
struct Finalizing {
    done_rx: oneshot::Receiver<AudioOutcome>,
    sink: PcmSink,
    frames: usize,
    interrupted: bool,
    /// WaitDone / Finish(pending_wait) 的回复端。取走即置 None，防 Cancel+done 重复回。
    reply: Option<oneshot::Sender<SpeakOutcome>>,
}

/// 一个 speak 实例的排队条目。
///
/// `pending` 缓存该实例尚未执行的命令；`started` 标记 run 已启动
/// （收到 WaitDone）——在此之前 Feed/Finish 只是缓存不执行（对齐 py：
/// feed 进实例私有 queue，`audio_chunks` 在 run 里才消费），保证实例的
/// 合成+播放不早于 shell 调度它的时机（如上一段 play_music 不被压声）；
/// `waiter` 是 run() 的 WaitDone 在流未收口时的挂起点；`stream_finished`
/// 标记 Finish 已执行。
struct Instance {
    token: u64,
    pending: VecDeque<TtsCmd>,
    started: bool,
    waiter: Option<oneshot::Sender<SpeakOutcome>>,
    stream_finished: bool,
}

async fn actor_loop(config: SeedConfig, mut inbox: mpsc::UnboundedReceiver<TtsCmd>) {
    // WS 连接：常驻复用，断开/失败时为 None，下次用前 ensure_connected 重连。
    let mut client: Option<SeedClient> = None;
    // 实例队列（按首命令到达序 FIFO）：只有队首实例会建 session / 发文本。
    // session / Finalizing 均属于队首实例，队首完成（WaitDone 已回、audio
    // 播完）即弹出，下一实例的 pending 才开始重放执行。
    let mut queue: VecDeque<Instance> = VecDeque::new();
    // 队首实例的 session（惰性建立：首个 Feed 触发）。
    let mut session: Option<Session> = None;
    // 队首实例的 Finalizing 态。
    let mut state: Option<Finalizing> = None;

    loop {
        // ---- Finalizing 态：等 audio done，期间收 Cancel ----
        if let Some(mut fin) = state.take() {
            let front_token = queue.front().map(|i| i.token);
            tokio::select! {
                biased;
                c = inbox.recv() => match c {
                    None => break,
                    Some(cmd) => {
                        if matches!(cmd, TtsCmd::Cancel { .. }) && Some(cmd.token()) == front_token {
                            // 队首被打断：停音频，回 interrupted，弹出，下一实例接棒。
                            fin.sink.stop();
                            let _ = fin.reply.take().map(|r| r.send(SpeakOutcome {
                                frames: fin.frames, interrupted: true,
                            }));
                            // 等 done 收尾（很快），避免泄漏 oneshot；丢弃结果。
                            let _ = fin.done_rx.await;
                            tracing::info!("finalize cancelled: audio stopped");
                            pop_front(&mut queue);
                        } else {
                            // 其它实例命令（或队首迟到杂音）：路由缓存。
                            route(cmd, &mut queue);
                            state = Some(fin);
                        }
                    }
                },
                audio = &mut fin.done_rx => {
                    // 自然播完（或 Cancel 触发的被打断完成）。
                    let interrupted = audio.map(|a| a.interrupted).unwrap_or(false) || fin.interrupted;
                    let _ = fin.reply.take().map(|r| r.send(SpeakOutcome {
                        frames: fin.frames, interrupted,
                    }));
                    tracing::info!(interrupted, "finalize done: audio finished");
                    pop_front(&mut queue);
                }
            }
            continue;
        }

        // ---- 队首实例命令消化（pending 优先于 inbox 新命令，保序）----
        // 门：实例 started（run 已启动）才执行 Feed/Finish/WaitDone；Cancel
        // 例外——未启动实例的取消若被压在 pending 里，会堵死整条队列。
        let digestible = queue
            .front()
            .map(|f| f.started || matches!(f.pending.front(), Some(TtsCmd::Cancel { .. })))
            .unwrap_or(false);
        if digestible {
            if let Some(cmd) = queue.front_mut().and_then(|f| f.pending.pop_front()) {
                match cmd {
                    TtsCmd::Feed { text, additions, .. } => {
                        tracing::info!(len = text.len(), "actor Feed");
                        // 建 session 前读活单元现值(run 起点已写入; 重复
                        // Feed 上 session 已存在, 现值被 ensure_session 忽略)
                        let adds = additions.lock().unwrap().clone();
                        if let Err(e) = ensure_session(
                            &mut client,
                            &mut session,
                            &config,
                            adds.as_deref().unwrap_or(""),
                        )
                        .await
                        {
                            tracing::warn!(?e, "tts feed: ensure_session failed");
                        }
                        if let (Some(c), Some(s)) = (client.as_mut(), session.as_mut()) {
                            if let Err(e) = c.send_text(&s.id, &text).await {
                                tracing::warn!(?e, "tts send_text failed");
                                // 连接可能已断：置 None，下次 ensure_session 重连。
                                client = None;
                            }
                        } else {
                            tracing::warn!("actor Feed: session is None after ensure_session");
                        }
                    }
                    TtsCmd::Finish { .. } => {
                        if let Err(e) = ensure_session(&mut client, &mut session, &config, "").await
                        {
                            tracing::warn!(?e, "tts finish: ensure_session failed");
                        }
                        if let (Some(c), Some(s)) = (client.as_mut(), session.as_mut()) {
                            if !s.finish_sent {
                                s.finish_sent = true;
                                if let Err(e) = c.finish_session(&s.id).await {
                                    tracing::warn!(?e, "tts finish_session failed");
                                    client = None;
                                }
                            }
                        }
                        // 已有 wait_done 在等 → 流收口了，进 Finalizing。否则置
                        // stream_finished，等 WaitDone 到达时直接 finalize。
                        let reply = queue.front_mut().and_then(|f| {
                            f.stream_finished = true;
                            f.waiter.take()
                        });
                        if let Some(reply) = reply {
                            if let Some(fin) =
                                enter_finalizing(&mut client, &mut session, reply).await
                            {
                                state = Some(fin);
                            } else {
                                // 空流：enter_finalizing 已回默认 outcome，实例完成。
                                pop_front(&mut queue);
                            }
                        }
                    }
                    TtsCmd::Cancel { .. } => {
                        // 队首打断（Idle/Active 态：session 还在或尚未建）。
                        if let Some(mut s) = session.take() {
                            s.interrupted = true;
                            s.sink.stop();
                            if let Some(c) = client.as_mut() {
                                let _ = c.cancel_session(&s.id).await;
                                let _ = wait_for_terminal_event(c).await;
                            } else {
                                // 连接已断：cancel 无意义，session 直接弃。
                            }
                        }
                        if let Some(reply) = queue.front_mut().and_then(|f| f.waiter.take()) {
                            let _ = reply.send(SpeakOutcome {
                                interrupted: true,
                                ..Default::default()
                            });
                        }
                        pop_front(&mut queue);
                    }
                    TtsCmd::WaitDone { reply, .. } => {
                        tracing::info!(has_session = session.is_some(), "actor WaitDone");
                        let stream_finished =
                            queue.front().map(|f| f.stream_finished).unwrap_or(false);
                        if stream_finished {
                            // 流已收口 → 进 Finalizing（drain + 等 audio，期间能收 Cancel）。
                            if let Some(fin) =
                                enter_finalizing(&mut client, &mut session, reply).await
                            {
                                state = Some(fin);
                            } else {
                                // 空流：enter_finalizing 已回默认 outcome，实例完成。
                                pop_front(&mut queue);
                            }
                        } else {
                            // 流尚未收口（body 可能还没到）：挂起，等 Finish/Cancel 触发。
                            if let Some(f) = queue.front_mut() {
                                f.waiter = Some(reply);
                            } else {
                                let _ = reply.send(SpeakOutcome::default());
                            }
                        }
                    }
                }
                continue;
            }
        }

        // ---- 无队首命令可消化：Active 收帧 / 等新命令 ----
        let has_active = session.as_ref().map(|s| !s.finished).unwrap_or(false);
        let cmd = if has_active {
            let c = client
                .as_mut()
                .expect("active session implies client present");
            let s = session.as_mut().unwrap();
            tokio::select! {
                biased;
                c = inbox.recv() => match c {
                    Some(c) => c,
                    None => break,
                },
                frame = c.next_event() => {
                    handle_frame(s, frame);
                    continue;
                }
            }
        } else {
            match inbox.recv().await {
                Some(c) => c,
                None => break,
            }
        };
        route(cmd, &mut queue);
    }

    // actor 退出兜底：若还在 Finalizing，把音频停掉避免泄漏。
    if let Some(fin) = state.take() {
        fin.sink.stop();
        let _ = fin.reply.map(|r| {
            r.send(SpeakOutcome {
                interrupted: true,
                ..Default::default()
            })
        });
    }
}

/// 把一条命令路由到其实例：已有实例 → 入该实例 pending；新 token → 尾插
/// 新实例条目。队首实例的命令也走 pending，由主循环逐条消化（保序）。
///
/// WaitDone 在路由时即置 started（run 已启动）：pending 里排在它前面的
/// Feed/Finish 随之放行。
///
/// 非队首实例的 Cancel 是特例：实例尚未开始，整条目移除即可（否则它会
/// 一直占着队列，堵死后续实例）。
fn route(cmd: TtsCmd, queue: &mut VecDeque<Instance>) {
    let token = cmd.token();
    if matches!(cmd, TtsCmd::Cancel { .. }) && queue.front().map(|f| f.token) != Some(token) {
        if let Some(pos) = queue.iter().position(|i| i.token == token) {
            if let Some(inst) = queue.remove(pos) {
                if let Some(reply) = inst.waiter {
                    let _ = reply.send(SpeakOutcome {
                        frames: 0,
                        interrupted: true,
                    });
                }
            }
        }
        return;
    }
    if let Some(inst) = queue.iter_mut().find(|i| i.token == token) {
        if matches!(cmd, TtsCmd::WaitDone { .. }) {
            inst.started = true;
        }
        inst.pending.push_back(cmd);
    } else {
        let mut inst = Instance {
            token,
            pending: VecDeque::new(),
            started: matches!(cmd, TtsCmd::WaitDone { .. }),
            waiter: None,
            stream_finished: false,
        };
        inst.pending.push_back(cmd);
        queue.push_back(inst);
    }
}

/// 队首实例完成，弹出。session/state 由调用方保证已清（enter_finalizing
/// take 走 / Cancel take 走）。
fn pop_front(queue: &mut VecDeque<Instance>) {
    if let Some(inst) = queue.pop_front() {
        tracing::debug!(
            token = inst.token,
            "instance done, next instance takes over"
        );
    }
}

/// 进入 Finalizing：take session → 确保发过 finish_session → drain 剩余 PCM →
/// 发 End → 把 done_rx/sink/frames 等字段提到 Finalizing 结构返回。
///
/// **不** await `s.done`（那会卡死 actor）—— 真正等 audio 的逻辑回到 actor_loop
/// 的 Finalizing 态 select 里，期间能收 Cancel。session 为 None（空流）时返回 None，
/// 调用方直接回 reply（enter 前已发）—— 但 reply 在这里已被 consume 进 None 分支处理。
async fn enter_finalizing(
    client: &mut Option<SeedClient>,
    session: &mut Option<Session>,
    reply: oneshot::Sender<SpeakOutcome>,
) -> Option<Finalizing> {
    let Some(mut s) = session.take() else {
        // 空流：没有音频要等，直接回默认 outcome。
        let _ = reply.send(SpeakOutcome::default());
        return None;
    };
    if !s.finish_sent {
        s.finish_sent = true;
        if let Some(c) = client.as_mut() {
            let _ = c.finish_session(&s.id).await;
        }
    }
    let t0 = std::time::Instant::now();
    drain_until_finished(client.as_mut(), &mut s).await;
    let drain_ms = t0.elapsed().as_millis();
    tracing::info!(frames = s.frames, drain_ms, "finalize_session drained");
    s.sink.end();
    Some(Finalizing {
        done_rx: s.done,
        sink: s.sink,
        frames: s.frames,
        interrupted: s.interrupted,
        reply: Some(reply),
    })
}

/// 确保有 client（常驻连接，断开则重连）+ session（本次会话，每次新建）。
///
/// 连接复用：client 为 None 时才 connect（建连 + StartConnection 握手），
/// 之后跨 session 常驻；session 每次都 StartSession 新建。
///
/// `additions`: 情绪指令（自然语言），仅新建 session 时用（已有未结束 session
/// 直接复用，指令被忽略）；空串 = 无指令，不下发。由首个 Feed 的活单元在建
/// session 前读现值（run 起点已写入）。
///
/// **stale 连接自愈**：常驻连接空闲久了会被服务端踢，首次操作才发现。故
/// `start_session` 失败时丢弃旧连接、重连一次再试（对齐 Python
/// `_ensure_connected` 的 stale ws 重连）——这样触发重连的那次 Feed 也不丢文本。
async fn ensure_session(
    client: &mut Option<SeedClient>,
    session: &mut Option<Session>,
    config: &SeedConfig,
    additions: &str,
) -> Result<(), RoutineError> {
    // 已有未结束的 session：直接复用。
    if let Some(s) = session.as_ref() {
        if !s.finished {
            return Ok(());
        }
    }
    // 旧 session 已结束（正常收口 / 连接断开被迫 finished）：丢弃，新建。
    *session = None;

    let id = Uuid::new_v4().to_string();

    // 先试现有常驻连接 start_session。
    if let Some(c) = client.as_mut() {
        if c.start_session(&id, &config.speaker_id, SAMPLE_RATE, additions)
            .await
            .is_ok()
        {
            let (sink, done) = audio()
                .play()
                .map_err(|e| RoutineError::Message(format!("audio play: {e}")))?;
            *session = Some(Session {
                id,
                sink,
                done,
                frames: 0,
                interrupted: false,
                finish_sent: false,
                finished: false,
            });
            return Ok(());
        }
        // start_session 失败：连接已死（被服务端踢等），丢弃重连。
        tracing::debug!("tts start_session failed on existing connection, reconnecting");
        *client = None;
    }

    // 重连 + start_session。
    let mut c = SeedClient::connect(config).await?;
    c.start_session(&id, &config.speaker_id, SAMPLE_RATE, additions)
        .await?;
    *client = Some(c);
    let (sink, done) = audio()
        .play()
        .map_err(|e| RoutineError::Message(format!("audio play: {e}")))?;
    *session = Some(Session {
        id,
        sink,
        done,
        frames: 0,
        interrupted: false,
        finish_sent: false,
        finished: false,
    });
    Ok(())
}

fn handle_frame(s: &mut Session, frame: Result<Frame, RoutineError>) {
    let frame = match frame {
        Ok(f) => f,
        Err(e) => {
            tracing::warn!(?e, "tts next_event failed");
            s.finished = true;
            return;
        }
    };
    let FrameKind::Event(event) = frame.kind else {
        return;
    };
    match event {
        Event::TtsResponse => {
            if !frame.payload.is_empty() {
                // 喂 TTS 的 24k/mono PCM，底层 audio 据此重采样到设备 48k/stereo。
                s.sink
                    .feed_pcm(frame.payload.as_ref(), SAMPLE_RATE, CHANNELS);
                s.frames += 1;
            }
        }
        Event::SessionFinished => {
            s.finished = true;
        }
        Event::SessionCanceled => {
            s.finished = true;
            s.interrupted = true;
        }
        Event::SessionFailed => {
            tracing::warn!(
                payload = %String::from_utf8_lossy(frame.payload.as_ref()),
                "tts session failed"
            );
            s.finished = true;
        }
        _ => {}
    }
}

/// 持续收 PCM 直到 SessionFinished（或被打断）。
async fn drain_until_finished(client: Option<&mut SeedClient>, s: &mut Session) {
    let Some(c) = client else {
        // 连接已断：无法继续 drain，直接收口。
        s.finished = true;
        return;
    };
    while !s.finished {
        tokio::select! {
            biased;
            frame = c.next_event() => {
                handle_frame(s, frame);
            }
            _ = tokio::time::sleep(std::time::Duration::from_millis(50)) => {
                // 兜底：避免无限等待（SessionFinished 漏发等异常）。
            }
        }
    }
}

async fn wait_for_terminal_event(client: &mut SeedClient) -> bool {
    let wait = tokio::time::timeout(std::time::Duration::from_millis(500), async {
        loop {
            let frame = client.next_event().await?;
            let FrameKind::Event(event) = frame.kind else {
                continue;
            };
            match event {
                Event::SessionFinished | Event::SessionCanceled => {
                    return Ok::<(), RoutineError>(())
                }
                Event::SessionFailed => {
                    return Err(RoutineError::Message(format!(
                        "tts session failed during cancel: {}",
                        String::from_utf8_lossy(frame.payload.as_ref())
                    )));
                }
                _ => {}
            }
        }
    })
    .await;
    matches!(wait, Ok(Ok(())))
}
