//! 进程级音频输出底层设备。
//!
//! 持有一个常驻 [`AudioOutput`]（默认输出设备，开在 48k/stereo）。所有 source
//! （TTS 的 24k/mono PCM）由 rodio mixer 各自重采样/声道转换到设备 rate 后实时
//! 混音——业界主流的"单设备 + 混音器按源重采样"做法。本模块是说话类 routine
//! 的共同底层，不依赖它们。

pub mod service;

use std::num::NonZero;
use std::sync::Arc;

use rodio::{buffer::SamplesBuffer, DeviceSinkBuilder, MixerDeviceSink, Player, Source};
use rodio::cpal::traits::{DeviceTrait, HostTrait};
use std::collections::HashSet;
use routine::core::RoutineError;

/// 共享输出设备的采样率。48k 是数字音视频工业默认，能满足常见音乐源（44.1k）。
pub(crate) const DEVICE_SAMPLE_RATE: u32 = 48_000;
/// 共享输出设备的声道数。stereo 保留音乐立体声；mono 说话源由 mixer 居中 up-mix。
pub(crate) const DEVICE_CHANNELS: u16 = 2;

/// 常驻本地音频输出设备。
///
/// 设备 stream 复用，单次播放使用独立 `Player`，避免 `stop()` 后队列不可继续追加。
pub(crate) struct AudioOutput {
    sink: Arc<MixerDeviceSink>,
}

impl AudioOutput {
    /// 打开输出设备，配置为 48k/stereo（共享混音设备速率）。
    /// `device: None` = 系统默认；`Some` = 按名选定（audio/output/set）。
    pub fn open_with(device: Option<rodio::Device>) -> Result<Self, RoutineError> {
        let builder = match device {
            Some(device) => DeviceSinkBuilder::from_device(device)
                .map_err(|e| RoutineError::Message(format!("open audio device: {e}")))?,
            None => DeviceSinkBuilder::from_default_device()
                .map_err(|e| RoutineError::Message(format!("open audio device: {e}")))?,
        };
        let mut sink = builder
            .with_channels(nz_u16(DEVICE_CHANNELS))
            .with_sample_rate(nz_u32(DEVICE_SAMPLE_RATE))
            .open_stream()
            .map_err(|e| RoutineError::Message(format!("open audio stream: {e}")))?;
        sink.log_on_drop(false);
        Ok(Self {
            sink: Arc::new(sink),
        })
    }

    /// 为一次播放创建新的队列，底层设备仍复用。
    pub fn new_playback(&self) -> AudioPlayback {
        AudioPlayback {
            _sink: Arc::clone(&self.sink),
            player: Arc::new(Player::connect_new(self.sink.mixer())),
        }
    }
}

/// 输出设备条目：`id` = ALSA PCM 名（唯一且跨重启稳定，set 的 key），
/// `name` = 人类可读显示名。
pub struct OutputInfo {
    pub id: String,
    pub name: String,
}

/// 设备 id（ALSA PCM 名）→ cpal 设备（精确匹配；"default" = 系统默认）。
pub(crate) fn find_output(id: &str) -> Result<Option<rodio::Device>, RoutineError> {
    let host = rodio::cpal::default_host();
    if id == "default" {
        return Ok(host.default_output_device());
    }
    let devices = host
        .output_devices()
        .map_err(|e| RoutineError::Message(format!("list output devices: {e}")))?;
    Ok(devices.into_iter().find(|d| {
        d.id().ok().is_some_and(|x| x.1 == id)
            && d.description().ok().is_some_and(|desc| desc.driver() != Some("null"))
    }))
}

/// 全部可用输出设备（default 在前）。null 虚拟 PCM 剔除；同一物理卡的
/// 多个 PCM 子设备显示名相同（如 Jetson 每张 hw 卡几十个 PCM），按显示名
/// 去重只留首个。蓝牙/bluealsa 虚拟 PCM 须在 /etc/asound.conf 有静态定义
/// 才会被枚举。
pub(crate) fn list_outputs() -> Vec<OutputInfo> {
    let host = rodio::cpal::default_host();
    let mut out = vec![OutputInfo {
        id: "default".to_string(),
        name: "system default".to_string(),
    }];
    let mut seen: HashSet<String> = HashSet::from(["system default".to_string()]);
    let Ok(devices) = host.output_devices() else { return out };
    for d in devices {
        let (Ok(desc), Ok(id)) = (d.description(), d.id()) else { continue };
        if desc.driver() == Some("null") {
            continue;
        }
        let trimmed = desc.name().trim();
        let name = if trimmed.is_empty() { id.1.clone() } else { trimmed.to_string() };
        if !seen.insert(name.clone()) {
            continue;
        }
        out.push(OutputInfo { id: id.1, name });
    }
    out
}

/// 单次播放会话的音频队列。
#[derive(Clone)]
pub(crate) struct AudioPlayback {
    _sink: Arc<MixerDeviceSink>,
    player: Arc<Player>,
}

impl AudioPlayback {
    /// 将一段 PCM 数据追加到播放队列。
    ///
    /// `data` 为 little-endian i16 字节序列；`sample_rate`/`channels` 是该 PCM 的
    /// 真实格式（调用方告知，底层不假设——TTS 传 24k/mono，其它源传各自格式）。
    /// rodio mixer 会把它重采样/声道转换到设备 48k/stereo 后混音。
    pub fn append_pcm(&self, data: &[u8], sample_rate: u32, channels: u16) {
        self.player.append(SamplesBuffer::new(
            nz_u16(channels),
            nz_u32(sample_rate),
            pcm_i16le_to_f32(data),
        ));
    }

    /// 把一个解码好的 `Source` 追加到播放队列。
    ///
    /// source 自带原生采样率/声道，rodio mixer 的 `UniformSourceIterator` 会
    /// 自动重采样/声道转换到设备 48k/stereo。入参取 `Box<dyn Source + Send>`：
    /// `dyn Source` 的 `Item = f32`，`Player::append` 的 `f32: FromSample<S::Item>`
    /// bound 由全局 impl 自动满足，无需在此命名 trait。
    pub fn append_source(&self, source: Box<dyn Source + Send + 'static>) {
        self.player.append(source);
    }

    /// 立即停止播放并清空队列。
    pub fn stop(&self) {
        self.player.stop();
    }

    /// 阻塞当前线程，等待最后一个 append 的 source 播完。
    ///
    /// 用 rodio `Player::sleep_until_end`：它等「最后一次 `append`」的完成
    /// signal。由于队列 FIFO 顺序播，最后一个 source 播完 ⟺ 此前所有 source
    /// 都已播完。signal 由 mixer 消费完该 source 时 fire（`go_next`），纯事件
    /// 驱动，无轮询。被 `stop()` 后 source 在 5ms 内连锁结束，signal 同样 fire
    /// 唤醒这里。调用方须放 `spawn_blocking`。
    pub fn wait_until_end(&self) {
        self.player.sleep_until_end();
    }
}

const PCM_I16_SCALE: f32 = 1.0 / 32768.0;

#[inline]
fn nz_u16(v: u16) -> NonZero<u16> {
    NonZero::new(v).expect("constant must be non-zero")
}

#[inline]
fn nz_u32(v: u32) -> NonZero<u32> {
    NonZero::new(v).expect("constant must be non-zero")
}

fn pcm_i16le_to_f32(bytes: &[u8]) -> Vec<f32> {
    let sample_count = bytes.len() / 2;
    let mut samples = vec![0.0; sample_count];
    pcm_i16le_fill_f32(bytes, &mut samples);
    samples
}

#[cfg(all(target_arch = "aarch64", target_endian = "little"))]
fn pcm_i16le_fill_f32(bytes: &[u8], out: &mut [f32]) {
    use std::arch::aarch64::{
        vcvtq_f32_s32, vdupq_n_f32, vget_high_s16, vget_low_s16, vld1q_s16, vmovl_s16, vmulq_f32,
        vst1q_f32,
    };

    let mut processed = 0usize;
    let chunk_count = out.len() / 8;
    let scale = unsafe { vdupq_n_f32(PCM_I16_SCALE) };

    while processed / 8 < chunk_count {
        let in_ptr = unsafe { bytes.as_ptr().add(processed * 2) as *const i16 };
        let out_ptr = unsafe { out.as_mut_ptr().add(processed) };
        unsafe {
            let samples = vld1q_s16(in_ptr);
            let low = vcvtq_f32_s32(vmovl_s16(vget_low_s16(samples)));
            let high = vcvtq_f32_s32(vmovl_s16(vget_high_s16(samples)));
            vst1q_f32(out_ptr, vmulq_f32(low, scale));
            vst1q_f32(out_ptr.add(4), vmulq_f32(high, scale));
        }
        processed += 8;
    }

    pcm_i16le_fill_f32_scalar(&bytes[processed * 2..], &mut out[processed..]);
}

#[cfg(not(all(target_arch = "aarch64", target_endian = "little")))]
fn pcm_i16le_fill_f32(bytes: &[u8], out: &mut [f32]) {
    pcm_i16le_fill_f32_scalar(bytes, out);
}

fn pcm_i16le_fill_f32_scalar(bytes: &[u8], out: &mut [f32]) {
    let len = out.len();
    let mut i = 0usize;
    while i < len {
        let base = i * 2;
        let sample = i16::from_le_bytes([bytes[base], bytes[base + 1]]);
        out[i] = sample as f32 * PCM_I16_SCALE;
        i += 1;
    }
}
