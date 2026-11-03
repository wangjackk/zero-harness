<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { RoutineScene } from '../lib/routineScene'

/* 视觉时间轴：奇点爆散为尘埃，尘埃聚合为恒星与行星；一次重点诞生由
   call 沿光迹抵达，create/start 字符绕轨道聚合。随后扩展到星系群并坍缩。 */
const playing = ref(true)
const phase = ref('奇点')
const ready = ref(false)
const failed = ref(false)
const rootRef = ref<HTMLDivElement>()
const canvasRef = ref<HTMLCanvasElement>()
let engine: RoutineScene | undefined
let resizeObserver: ResizeObserver | undefined
let themeObserver: MutationObserver | undefined
let disposed = false
let media: MediaQueryList | undefined

function syncPlayback() {
  // 固定背景在首页始终存在；首屏滚出视口不等于动画应当暂停。
  engine?.setActive(playing.value && ready.value && !failed.value)
}
watch([playing, ready, failed], syncPlayback)

function onPointerMove(event: PointerEvent) {
  if (!rootRef.value || media?.matches) return
  const rect = rootRef.value.getBoundingClientRect()
  engine?.setPointer(
    ((event.clientX - rect.left) / rect.width - .5) * 2,
    ((event.clientY - rect.top) / rect.height - .5) * 2
  )
}
function onPointerLeave() {
  engine?.setPointer(0, 0)
}
function onMotionChange(event: MediaQueryListEvent) {
  if (event.matches) playing.value = false
}
function onContextLost(event: Event) {
  event.preventDefault()
  failed.value = true
}

onMounted(async () => {
  media = window.matchMedia('(prefers-reduced-motion: reduce)')
  if (media.matches) playing.value = false
  media.addEventListener('change', onMotionChange)
  canvasRef.value?.addEventListener('webglcontextlost', onContextLost)

  if (rootRef.value) {
    resizeObserver = new ResizeObserver(([entry]) => {
      engine?.resize(entry.contentRect.width, entry.contentRect.height)
    })
    resizeObserver.observe(rootRef.value)
  }

  try {
    if (!canvasRef.value || !rootRef.value) return
    const { RoutineScene } = await import('../lib/routineScene')
    if (disposed) return
    engine = new RoutineScene(canvasRef.value, value => { phase.value = value })
    const rect = rootRef.value.getBoundingClientRect()
    engine.resize(rect.width, rect.height)
    engine.setLight(document.documentElement.dataset.theme === 'light')
    if (media.matches) engine.showComplete()
    const previewTime = Number(new URLSearchParams(window.location.search).get('sceneTime'))
    if (Number.isFinite(previewTime) && previewTime > 0) {
      engine.showAt(previewTime)
      playing.value = false
    }
    themeObserver = new MutationObserver(() => {
      engine?.setLight(document.documentElement.dataset.theme === 'light')
    })
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    ready.value = true
    syncPlayback()
  } catch (error) {
    console.warn('三维主景不可用，已显示静态构图。', error)
    failed.value = true
  }
})

onBeforeUnmount(() => {
  disposed = true
  resizeObserver?.disconnect()
  themeObserver?.disconnect()
  media?.removeEventListener('change', onMotionChange)
  canvasRef.value?.removeEventListener('webglcontextlost', onContextLost)
  engine?.dispose()
})
</script>

<template>
  <div
    ref="rootRef"
    class="rf"
    :class="{ 'is-ready': ready && !failed, 'is-fallback': failed }"
    role="img"
    aria-label="三维宇宙循环：奇点爆发为尘埃并聚合为恒星；调用代码沿光迹抵达，创建与启动字符绕轨道聚合成行星；镜头扩展到星系群，随后坍缩回奇点"
    @pointermove="onPointerMove"
    @pointerleave="onPointerLeave"
  >
    <canvas ref="canvasRef" class="rf-canvas" aria-hidden="true" />
    <div class="rf-vignette" aria-hidden="true" />
    <div v-if="failed" class="rf-fallback" aria-hidden="true">
      <i class="rf-fallback-orbit orbit-a" /><i class="rf-fallback-orbit orbit-b" /><i class="rf-fallback-orbit orbit-c" />
      <i class="rf-fallback-star" /><i class="rf-fallback-body body-a" /><i class="rf-fallback-body body-b" /><i class="rf-fallback-body body-c" />
      <i class="rf-fallback-sibling" />
    </div>
    <Teleport to="body">
      <div class="rf-chrome">
        <span class="rf-caption mono"><i />COSMIC CYCLE · {{ phase }}</span>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.rf {
  position: relative;
  overflow: hidden;
  user-select: none;
  background:
    radial-gradient(ellipse at 68% 45%, rgba(32, 85, 123, .09), transparent 43%),
    radial-gradient(ellipse at 86% 20%, rgba(92, 57, 142, .065), transparent 35%);
}
.rf-canvas { position: absolute; inset: 0; display: block; width: 100%; height: 100%; opacity: 0; transition: opacity 1.2s ease; }
.is-ready .rf-canvas { opacity: 1; }
.rf-vignette {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(90deg, var(--bg) 0%, color-mix(in oklab, var(--bg) 76%, transparent) 18%, transparent 46%),
    linear-gradient(0deg, var(--bg) 0%, transparent 18%, transparent 88%, color-mix(in oklab, var(--bg) 45%, transparent) 100%);
}
.rf-chrome {
  position: fixed;
  right: clamp(20px, 5vw, 58px);
  bottom: clamp(34px, 6vh, 64px);
  display: flex;
  align-items: center;
  gap: 16px;
  z-index: 3;
  pointer-events: none;
}
.rf-caption { display: inline-flex; align-items: center; gap: 8px; font-size: 8px; letter-spacing: .14em; color: var(--ink-4); }
.rf-caption i { width: 20px; height: 1px; background: var(--line-3); }

/* 无 WebGL 时保留恒星系与远处星体的静态构图。 */
.rf-fallback { position: absolute; inset: 0; }
.rf-fallback-star, .rf-fallback-body, .rf-fallback-sibling, .rf-fallback-orbit { position: absolute; display: block; border-radius: 50%; }
.rf-fallback-star {
  left: 65%; top: 43%; width: 150px; height: 150px; transform: translate(-50%, -50%);
  background: radial-gradient(circle at 38% 32%, #fff 0 5%, #bdf5ff 20%, #48b8dd 48%, #15537c 72%);
  box-shadow: 0 0 35px #65d8ff88, 0 0 110px #399ddd44, 0 0 220px #386bc522;
}
.rf-fallback-orbit { left: 65%; top: 43%; transform: translate(-50%, -50%) rotate(-12deg); border: 1px solid rgba(120, 211, 235, .17); }
.orbit-a { width: 310px; height: 126px; }
.orbit-b { width: 480px; height: 205px; border-color: rgba(174, 145, 234, .15); }
.orbit-c { width: 650px; height: 290px; }
.rf-fallback-body { width: 18px; height: 18px; background: #91dcee; box-shadow: 0 0 22px #72d9ff88; }
.body-a { left: 73%; top: 39%; }
.body-b { left: 52%; top: 49%; width: 26px; height: 26px; background: #a594d5; }
.body-c { left: 83%; top: 51%; width: 13px; height: 13px; }
.rf-fallback-sibling { left: 87%; top: 22%; width: 20px; height: 20px; background: #d6c7ff; box-shadow: 0 0 40px #9b7feaaa; }
html[data-theme='light'] .rf { background: radial-gradient(ellipse at 68% 45%, rgba(40, 145, 180, .08), transparent 50%); }
html[data-theme='light'] .rf-canvas { opacity: .72; }
@media (max-width: 1100px) {
  .rf { -webkit-mask-image: linear-gradient(0deg, transparent, #000 12%, #000 90%, transparent); mask-image: linear-gradient(0deg, transparent, #000 12%, #000 90%, transparent); }
  .rf-vignette { background: linear-gradient(0deg, var(--bg), transparent 15%, transparent 85%, var(--bg)); }
  .rf-chrome { right: 24px; bottom: 22px; }
  .rf-fallback-star, .rf-fallback-orbit { left: 50%; }
  .body-b { left: 32%; }
}
@media (max-width: 760px) {
  .rf-vignette { background: linear-gradient(180deg, var(--bg) 0%, color-mix(in oklab, var(--bg) 86%, transparent) 38%, transparent 66%, var(--bg) 100%); }
  .rf-caption { display: none; }
  .rf-chrome { right: 18px; bottom: 14px; }
  .rf-fallback-star { width: 100px; height: 100px; }
  .orbit-a { width: 220px; height: 100px; }
  .orbit-b { width: 330px; height: 150px; }
  .orbit-c { width: 440px; height: 205px; }
}
@media (prefers-reduced-motion: reduce) {
  .rf-canvas { transition: none; }
}
</style>
