//! 进程级音频播放服务（说话类 routine 的共同底层）。
//!
//! 持有一个**懒开/空闲关**的 [`AudioOutput`]（默认或经 `audio/set_output`
//! 选定的输出设备）。每次播放经
//! [`AudioService::play`] 拿到一个 [`PcmSink`] + 完成信号：调用方把 PCM 帧
//! 经 sink 喂进来，服务内部一个 task 独占 [`AudioPlayback`]，负责 append +
//! 收口时 drain 设备队列直到播完，再回 [`AudioOutcome`]。
//!
//! **设备生命周期**：cpal/ALSA 回调线程一旦开流就持续拉采样，mixer 空（静音）
//! 也耗 CPU（Jetson Nano 上约 10%）。故不在启动时开流，而是首次播放懒开；
//! 所有播放结束（active 归零）后等 [`IDLE_CLOSE_TIMEOUT`]，仍空闲就关流，下次
//! 播放重开。代价是空闲后再说话开头有一次 ALSA 重开流延迟（~50-150ms 首音
//! gap），换 sustained 0% idle。
//!
//! 接缝是一条 typed channel（[`AudioMsg`]）：调用方只持有 `PcmSink`（一个 sender
//! 薄封装），不 import 任何 rodio 类型、不碰等待逻辑——「等播完」完全归拥有设备的
//! 一侧，避免等待逻辑跨 task / 跨函数割裂导致的时序对不齐。

use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use rodio::Source;
use tokio::sync::{mpsc, oneshot};

use routine::core::RoutineError;

use super::AudioOutput;

/// 所有播放结束后，再等这么久仍空闲就关设备流（换 idle 0%）。
const IDLE_CLOSE_TIMEOUT: Duration = Duration::from_secs(3);

/// 一次播放的完成结果。
#[derive(Debug, Clone, Default)]
pub struct AudioOutcome {
    /// 是否被打断（`Stop` 收口）。
    pub interrupted: bool,
}

/// 调用方 → Audio 单向消息（接缝）。调用方只发，Audio 单 task 收。
enum AudioMsg {
    /// 一片 PCM（little-endian i16）+ 该 PCM 的真实采样率/声道。
    Pcm(Vec<u8>, u32, u16),
    /// 一个解码好的 source，追加到播放队列。
    Source(Box<dyn Source + Send + 'static>),
    /// 收口：drain 设备队列直到播完，发完成（非打断）。
    End,
    /// 打断：立即清空队列，发打断 outcome。
    Stop,
}

/// 播放句柄：往里喂 PCM / Source / 收口 / 打断。
///
/// 实质是一个 `mpsc::UnboundedSender<AudioMsg>` 的薄封装，天然
/// `Send + Sync`。所有方法发即走（非阻塞）。
#[derive(Clone)]
pub struct PcmSink {
    tx: mpsc::UnboundedSender<AudioMsg>,
}

impl PcmSink {
    /// 喂一片 PCM。空数据忽略。`sample_rate`/`channels` 是该 PCM 的真实格式。
    pub fn feed_pcm(&self, data: &[u8], sample_rate: u32, channels: u16) {
        if !data.is_empty() {
            let _ = self
                .tx
                .send(AudioMsg::Pcm(data.to_vec(), sample_rate, channels));
        }
    }
    /// 喂一个解码好的 source（追加到播放队列）。
    pub fn feed_source<S>(&self, source: S)
    where
        S: Source + Send + 'static,
    {
        let _ = self.tx.send(AudioMsg::Source(Box::new(source)));
    }
    /// 收口：通知 Audio drain 设备队列直到播完。
    pub fn end(&self) {
        let _ = self.tx.send(AudioMsg::End);
    }
    /// 打断：通知 Audio 立即停止播放。
    pub fn stop(&self) {
        let _ = self.tx.send(AudioMsg::Stop);
    }
}

/// 当前持有的设备 + 活跃播放计数。`active==0` 时可关流。
struct SinkState {
    output: Arc<AudioOutput>,
    active: usize,
}

/// 进程级播放服务单例。
pub struct AudioService {
    state: Mutex<Option<SinkState>>,
    /// 选定输出设备 id（None = 系统默认）。切换只影响下一次开流：在播会话
    /// 挂旧 stream 由 task 持有自然播完，空闲后懒开用新设备。
    selected: Mutex<Option<String>>,
}

static SVC: OnceLock<AudioService> = OnceLock::new();

impl AudioService {
    /// 获取进程单例。不在此时开流——首次 [`AudioService::play`] 才懒开。
    pub fn open() -> &'static Self {
        SVC.get_or_init(|| AudioService {
            state: Mutex::new(None),
            selected: Mutex::new(None),
        })
    }

    /// 取设备引用，必要时懒开流。每次 +1 活跃计数。
    fn acquire(&self) -> Result<Arc<AudioOutput>, RoutineError> {
        // 按选定名解析设备（锁外做，避免与 state 锁嵌套）。选定设备已拔出/
        // 蓝牙断开 → 静默回退系统默认（speak 不能因设备消失而炸）。
        let device = {
            let selected = self.selected.lock().expect("audio selected poisoned");
            match selected.as_deref() {
                Some(name) => match super::find_output(name) {
                    Ok(found) => found,
                    Err(e) => {
                        tracing::warn!("find output device {name} failed: {e}; fallback default");
                        None
                    }
                },
                None => None,
            }
        };
        let mut state = self.state.lock().expect("audio state poisoned");
        if state.is_none() {
            let output = Arc::new(AudioOutput::open_with(device)?);
            *state = Some(SinkState { output, active: 0 });
        }
        let s = state.as_mut().expect("sink state present");
        s.active += 1;
        Ok(Arc::clone(&s.output))
    }

    /// 查询输出设备状态：`(当前选定 id(None = 系统默认), 可用设备列表)`。
    pub fn output_status(&self) -> (Option<String>, Vec<super::OutputInfo>) {
        let current = self.selected.lock().expect("audio selected poisoned").clone();
        (current, super::list_outputs())
    }

    /// 设置输出设备（id = ALSA PCM 名）：校验存在 → 存为选定 → 关空闲流
    /// （若在播则不动，在播挂旧流播完，空闲关流后下一次懒开新设备）。
    /// 返回当前选定 id；设备不存在报错（消息内带可用 id 列表）。
    pub fn set_output(&self, id: &str) -> Result<Option<String>, RoutineError> {
        if super::find_output(id)?.is_none() {
            let available: Vec<String> = super::list_outputs().into_iter().map(|o| o.id).collect();
            return Err(RoutineError::Message(format!(
                "output device not found: {id}, available: {available:?}"
            )));
        }
        *self.selected.lock().expect("audio selected poisoned") = Some(id.to_string());
        // active==0 → 立即关流（下次懒开新设备）；在播则留给空闲关流。
        let mut state = self.state.lock().expect("audio state poisoned");
        if matches!(state.as_ref(), Some(s) if s.active == 0) {
            *state = None;
        }
        drop(state);
        Ok(Some(id.to_string()))
    }

    /// 一次播放结束：-1 活跃计数。归零则排一个空闲关流定时器。
    fn release(&self) {
        let schedule_idle = {
            let mut state = self.state.lock().expect("audio state poisoned");
            match state.as_mut() {
                Some(s) => {
                    s.active = s.active.saturating_sub(1);
                    s.active == 0
                }
                None => false,
            }
        };
        if schedule_idle {
            // self 非 'static，从单例拿 &'static 给 spawn 的定时器。
            let svc = SVC.get().expect("audio service initialized");
            tokio::spawn(async move {
                tokio::time::sleep(IDLE_CLOSE_TIMEOUT).await;
                let mut state = svc.state.lock().expect("audio state poisoned");
                if let Some(s) = state.as_ref() {
                    if s.active == 0 {
                        tracing::debug!(
                            "audio idle {}s, closing device stream",
                            IDLE_CLOSE_TIMEOUT.as_secs()
                        );
                        *state = None; // drop SinkState → output/sink Arc 释放 → 流关
                    }
                }
            });
        }
    }

    /// 开新的播放队列，返回 sink（喂 PCM/Source/收口/打断）+ 完成信号。
    ///
    /// 内部 spawn 一个 task 独占这个 [`AudioPlayback`]：收 `Pcm` → append；
    /// 收 `Source` → append；收 `End` → drain 设备队列直到播完；收 `Stop` →
    /// 立即停。任一收口后回 [`AudioOutcome`]，并 release 活跃计数（可能触发
    /// 空闲关流）。调用方持有 sink + done receiver，等播完就 `await` 那个
    /// receiver——等待逻辑与设备状态同处一个 task，时序自洽。
    pub fn play(&self) -> Result<(PcmSink, oneshot::Receiver<AudioOutcome>), RoutineError> {
        let output = self.acquire()?;
        let playback = output.new_playback();
        let (msg_tx, mut msg_rx) = mpsc::unbounded_channel::<AudioMsg>();
        let (done_tx, done_rx) = oneshot::channel::<AudioOutcome>();
        let svc = SVC.get().expect("audio service initialized");

        tokio::spawn(async move {
            let mut interrupted = false;
            loop {
                match msg_rx.recv().await {
                    Some(AudioMsg::Pcm(data, sr, ch)) => playback.append_pcm(&data, sr, ch),
                    Some(AudioMsg::Source(src)) => playback.append_source(src),
                    Some(AudioMsg::End) => {
                        // 等最后一个 source 的完成 signal（spawn_blocking 不堵 reactor）。
                        // 关键：等待期间仍要听 Stop —— 否则 interrupt 时 Stop 堆在
                        // msg_rx 没人处理，音频会继续播完（曾出现的 bug）。
                        // Stop 来 → playback.stop() 唤醒 sleep_until_end 提前返回。
                        let p = playback.clone();
                        let wait_fut = tokio::task::spawn_blocking(move || p.wait_until_end());
                        tokio::select! {
                            _ = wait_fut => {
                                // 自然播完。select 命中即完成，不再 await（避免
                                // "JoinHandle polled after completion" panic）。
                            }
                            _ = msg_rx.recv() => {
                                // 等待期间收到消息（Stop / None）：停音频。
                                playback.stop();
                                interrupted = true;
                                // wait_fut 被 stop 唤醒后会自行结束（spawn_blocking
                                // task 不 await 不泄漏）。
                            }
                        }
                        break;
                    }
                    Some(AudioMsg::Stop) => {
                        playback.stop();
                        interrupted = true;
                        break;
                    }
                    None => {
                        // sink 被丢弃（session 异常结束）：停掉避免尾音泄漏。
                        playback.stop();
                        break;
                    }
                }
            }
            let _ = done_tx.send(AudioOutcome { interrupted });
            // 显式释放 playback / output 引用后再 release，确保关流定时器到期时
            // 它们已 drop（sink Arc 只剩 SinkState 一份）。
            drop(playback);
            drop(output);
            svc.release();
        });

        Ok((PcmSink { tx: msg_tx }, done_rx))
    }

    /// 为一次播放喂一个解码好的 `Source`（如 wav `Decoder`），复用底层共享设备。
    ///
    /// 与 [`AudioService::play`] 共用同一套 task/消息通道：投 `Source`（追加到队列）
    /// 后立即 `End`（drain 到播完），返回 sink（用于打断）+ 完成信号。
    pub fn play_source<S>(
        &self,
        source: S,
    ) -> Result<(PcmSink, oneshot::Receiver<AudioOutcome>), RoutineError>
    where
        S: Source + Send + 'static,
    {
        let (sink, done_rx) = self.play()?;
        sink.feed_source(source);
        sink.end();
        Ok((sink, done_rx))
    }
}

/// 进程单例 getter。调用前须先 [`AudioService::open`]。
pub fn audio() -> &'static AudioService {
    AudioService::open()
}
