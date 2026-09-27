import { onUnmounted } from 'vue'
import { useWS } from './useWS'

/**
 * 接收服务端推送的 PCM 音频流并无缝播放.
 *
 * 协议 (speak routine 经 WebServer._broadcast_bytes 直推 binary, 零拷贝):
 *   JSON  { type: 'audio_start', sample_rate, channels }  -- 初始化 AudioContext
 *   BIN   <Int16 PCM bytes>                               -- 立即调度播放
 *   JSON  { type: 'audio_end' }                           -- 标记流结束
 */
export function useAudioPlayer(ws: ReturnType<typeof useWS>): void {
  let audioCtx: AudioContext | null = null
  let nextPlayTime = 0
  let channels = 1
  let active = false
  const sources = new Set<AudioBufferSourceNode>()

  // 浏览器要求 AudioContext 在用户手势后才能播放.
  // 在页面首次点击时预热 context,保证后续服务端推音频时已经 running.
  function warmup() {
    if (!audioCtx) audioCtx = new AudioContext({ sampleRate: 24000 })
    if (audioCtx.state === 'suspended') audioCtx.resume()
    document.removeEventListener('click', warmup)
  }
  document.addEventListener('click', warmup)

  const unsubStart = ws.on('audio_start', (msg) => {
    const sampleRate = (msg.sample_rate as number) ?? 24000
    channels = (msg.channels as number) ?? 1
    if (!audioCtx || audioCtx.sampleRate !== sampleRate) {
      audioCtx?.close()
      audioCtx = new AudioContext({ sampleRate })
    }
    if (audioCtx.state === 'suspended') audioCtx.resume()
    nextPlayTime = audioCtx.currentTime
    active = true
  })

  const unsubEnd = ws.on('audio_end', () => {
    active = false
    if (!audioCtx) {
      ws.send({ type: 'audio_playback_done' })
      return
    }
    const remaining = nextPlayTime - audioCtx.currentTime
    if (remaining <= 0) {
      ws.send({ type: 'audio_playback_done' })
    } else {
      setTimeout(() => ws.send({ type: 'audio_playback_done' }), remaining * 1000 + 80)
    }
  })

  const unsubBinary = ws.onBinary((data: ArrayBuffer) => {
    if (!active || !audioCtx) return

    // Int16 PCM → Float32(Web Audio API 需要 float32)
    const pcm = new Int16Array(data)
    const frameCount = Math.floor(pcm.length / channels)
    if (frameCount === 0) return

    const buffer = audioCtx.createBuffer(channels, frameCount, audioCtx.sampleRate)
    for (let ch = 0; ch < channels; ch++) {
      const channelData = buffer.getChannelData(ch)
      for (let i = 0; i < frameCount; i++) {
        channelData[i] = pcm[i * channels + ch] / 32768
      }
    }

    const source = audioCtx.createBufferSource()
    source.buffer = buffer
    source.connect(audioCtx.destination)
    sources.add(source)
    source.onended = () => sources.delete(source)

    const startAt = Math.max(audioCtx.currentTime, nextPlayTime)
    source.start(startAt)
    nextPlayTime = startAt + buffer.duration
  })

  onUnmounted(() => {
    document.removeEventListener('click', warmup)
    unsubStart()
    unsubEnd()
    unsubBinary()
    audioCtx?.close()
    audioCtx = null
  })
}
