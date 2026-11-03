<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { NAMES } from '../data/names'
import { bandOf, BAND_MAP } from '../data/bands'

/* 注册表的星空：每个点就是一个注册名。鼠标靠近时亮起并连线 —— 这是“同一张表”的视觉。 */

const host = ref<HTMLDivElement | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)
let raf = 0
let ro: ResizeObserver | null = null
let ctx: CanvasRenderingContext2D | null = null
let w = 0
let h = 0
let dpr = 1

interface P { x: number; y: number; vx: number; vy: number; r: number; c: string; n: string; z: number }
let pts: P[] = []
let pointer = { x: -999, y: -999, on: false }

const rows = (NAMES as any[]).filter(r => !!r.name)

function accentOf(name: string) {
  const b = bandOf(name)
  return b === 'other' ? '#8A94A6' : BAND_MAP[b].accent
}

function build() {
  const el = canvas.value
  if (!el || !host.value) return
  const rect = host.value.getBoundingClientRect()
  dpr = Math.min(window.devicePixelRatio || 1, 2)
  w = rect.width
  h = rect.height
  el.width = Math.floor(w * dpr)
  el.height = Math.floor(h * dpr)
  el.style.width = w + 'px'
  el.style.height = h + 'px'
  ctx = el.getContext('2d')
  ctx?.setTransform(dpr, 0, 0, dpr, 0, 0)

  pts = rows.map((r, i) => {
    const z = 0.35 + Math.random() * 0.65
    return {
      x: Math.random() * w,
      y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.12 * z,
      vy: (Math.random() - 0.5) * 0.12 * z,
      r: 0.7 + z * 1.3,
      c: accentOf(r.name),
      n: r.name,
      z
    }
  })
}

function draw() {
  if (!ctx) return
  ctx.clearRect(0, 0, w, h)
  const light = document.documentElement.dataset.theme === 'light'
  const baseLine = light ? '15,23,42' : '255,255,255'

  // 近邻细线
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i]
    for (let j = i + 1; j < pts.length; j++) {
      const b = pts[j]
      const dx = a.x - b.x
      const dy = a.y - b.y
      const d2 = dx * dx + dy * dy
      if (d2 > 120 * 120) continue
      const t = 1 - Math.sqrt(d2) / 120
      ctx.strokeStyle = `rgba(${baseLine},${(light ? 0.1 : 0.13) * t * t})`
      ctx.lineWidth = 0.6
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(b.x, b.y)
      ctx.stroke()
    }
  }

  // 鼠标场
  if (pointer.on) {
    const near: { p: P; d: number }[] = []
    for (const p of pts) {
      const dx = p.x - pointer.x
      const dy = p.y - pointer.y
      const d = Math.hypot(dx, dy)
      if (d < 190) {
        near.push({ p, d })
        p.vx += (dx / (d + 1)) * 0.014
        p.vy += (dy / (d + 1)) * 0.014
      }
    }
    near.sort((a, b) => a.d - b.d)
    for (const { p, d } of near.slice(0, 7)) {
      const t = 1 - d / 190
      ctx.strokeStyle = `rgba(${baseLine},${0.22 * t})`
      ctx.lineWidth = 0.7
      ctx.beginPath()
      ctx.moveTo(pointer.x, pointer.y)
      ctx.lineTo(p.x, p.y)
      ctx.stroke()
    }
    ctx.fillStyle = `rgba(${baseLine},0.5)`
    ctx.beginPath()
    ctx.arc(pointer.x, pointer.y, 1.6, 0, Math.PI * 2)
    ctx.fill()
  }

  // 点
  for (const p of pts) {
    const near = pointer.on && Math.hypot(p.x - pointer.x, p.y - pointer.y) < 150
    ctx.globalAlpha = near ? 0.95 : 0.28 + p.z * 0.45
    ctx.fillStyle = p.c
    ctx.beginPath()
    ctx.arc(p.x, p.y, near ? p.r + 1.1 : p.r, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.globalAlpha = 1

  // 最靠近光标的那个名字
  if (pointer.on) {
    let best: P | null = null
    let bd = 120
    for (const p of pts) {
      const d = Math.hypot(p.x - pointer.x, p.y - pointer.y)
      if (d < bd) { bd = d; best = p }
    }
    if (best) {
      ctx.font = '500 10.5px "JetBrains Mono Variable", ui-monospace, monospace'
      ctx.fillStyle = light ? 'rgba(15,23,42,.62)' : 'rgba(237,240,246,.72)'
      ctx.fillText(best.n, best.x + 8, best.y - 8)
    }
  }
}

function tick() {
  for (const p of pts) {
    p.x += p.vx
    p.y += p.vy
    p.vx *= 0.995
    p.vy *= 0.995
    if (Math.abs(p.vx) < 0.02) p.vx += (Math.random() - 0.5) * 0.02
    if (Math.abs(p.vy) < 0.02) p.vy += (Math.random() - 0.5) * 0.02
    if (p.x < -20) p.x = w + 20
    if (p.x > w + 20) p.x = -20
    if (p.y < -20) p.y = h + 20
    if (p.y > h + 20) p.y = -20
  }
  draw()
  raf = requestAnimationFrame(tick)
}

function onMove(e: PointerEvent) {
  const r = host.value?.getBoundingClientRect()
  if (!r) return
  pointer = { x: e.clientX - r.left, y: e.clientY - r.top, on: true }
}
function onLeave() { pointer.on = false }

onMounted(() => {
  build()
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    draw()
  } else {
    raf = requestAnimationFrame(tick)
  }
  ro = new ResizeObserver(() => { build(); if (!raf) draw() })
  if (host.value) ro.observe(host.value)
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerleave', onLeave)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  ro?.disconnect()
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerleave', onLeave)
})
</script>

<template>
  <div ref="host" class="sky" aria-hidden="true">
    <canvas ref="canvas" />
  </div>
</template>

<style scoped>
.sky { position: absolute; inset: 0; overflow: hidden; }
canvas { display: block; width: 100%; height: 100%; }
</style>
