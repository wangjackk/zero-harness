<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ROUTES, ROUTE_GROUPS } from '../router'
import { openPalette } from '../lib/palette'
import { useTheme } from '../lib/theme'
import { BANDS } from '../data/bands'

const route = useRoute()
const { dark, toggle } = useTheme()
const scrolled = ref(false)
const progress = ref(0)
const hd = ref<HTMLElement | null>(null)
let headerObserver: ResizeObserver | undefined

const pts = BANDS.map((b, i) => {
  const a = ((-90 + i * (360 / 7)) * Math.PI) / 180
  return { x: 14 + 9 * Math.cos(a), y: 14 + 9 * Math.sin(a), c: b.accent }
})

function onScroll() {
  const y = window.scrollY
  scrolled.value = y > 8
  const h = document.documentElement.scrollHeight - window.innerHeight
  progress.value = h > 0 ? Math.min(1, y / h) : 0
}

function syncHeaderHeight() {
  if (!hd.value) return
  const height = Math.ceil(hd.value.getBoundingClientRect().height)
  document.documentElement.style.setProperty('--nav-h', `${height}px`)
}

onMounted(() => {
  onScroll()
  syncHeaderHeight()
  if (hd.value) {
    headerObserver = new ResizeObserver(syncHeaderHeight)
    headerObserver.observe(hd.value)
  }
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', onScroll)
})
onUnmounted(() => {
  headerObserver?.disconnect()
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', onScroll)
})
</script>

<template>
  <header ref="hd" class="hd" :class="{ scrolled }">
    <div class="hd-in">
      <RouterLink to="/" class="brand" aria-label="Routine Atlas 首页">
        <svg class="mark" width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
          <circle cx="14" cy="14" r="9" fill="none" stroke="currentColor" stroke-opacity=".28" stroke-dasharray="1.5 3" />
          <circle cx="14" cy="14" r="2.4" :fill="dark ? '#EDF0F6' : '#10141B'" />
          <circle v-for="(p, i) in pts" :key="i" :cx="p.x" :cy="p.y" r="1.7" :fill="p.c" />
        </svg>
        <span class="brand-txt">
          <span class="brand-name">ROUTINE ATLAS</span>
          <span class="brand-sub">zero-harness · 单元拓扑</span>
        </span>
      </RouterLink>

      <nav class="nav" aria-label="主导航">
        <div v-for="g in ROUTE_GROUPS" :key="g.id" class="nav-group" :class="`group-${g.id}`">
          <RouterLink
            v-for="r in ROUTES.filter(x => x.group === g.id)"
            :key="r.path"
            :to="r.path"
            class="nav-a"
            :class="{ on: route.path === r.path }"
          >
            <span class="nav-n">{{ r.no }}</span>
            <span class="nav-t">{{ r.title }}</span>
          </RouterLink>
        </div>
      </nav>

      <div class="hd-right">
        <button class="btn btn-quiet kbd-btn" @click="openPalette" aria-label="搜索与跳转">
          <span class="kbd-btn-t">搜索</span>
          <kbd>Ctrl K</kbd>
        </button>
        <button class="icon-btn" @click="toggle" :aria-label="dark ? '切换至浅色主题' : '切换至深色主题'">
          <svg v-if="dark" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <circle cx="8" cy="8" r="3.2" stroke="currentColor" stroke-width="1.3" />
            <g stroke="currentColor" stroke-width="1.3" stroke-linecap="round">
              <path d="M8 1v1.6M8 13.4V15M1 8h1.6M13.4 8H15M3.05 3.05l1.13 1.13M11.82 11.82l1.13 1.13M12.95 3.05l-1.13 1.13M4.18 11.82l-1.13 1.13" />
            </g>
          </svg>
          <svg v-else width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M13.2 9.8A5.6 5.6 0 0 1 6.2 2.8a5.6 5.6 0 1 0 7 7Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
          </svg>
        </button>
      </div>
    </div>
    <div class="progress" :style="{ transform: `scaleX(${progress})` }" />
  </header>
</template>

<style scoped>
.hd {
  position: fixed;
  inset: 0 0 auto;
  min-height: 64px;
  z-index: 100;
  border-bottom: 1px solid transparent;
  transition: background 0.3s var(--ease), border-color 0.3s var(--ease), backdrop-filter 0.3s var(--ease);
}
.hd.scrolled {
  background: color-mix(in oklab, var(--bg) 78%, transparent);
  backdrop-filter: blur(14px) saturate(1.2);
  border-bottom-color: var(--line);
}
.hd-in {
  max-width: 1600px;
  min-height: 64px;
  margin: 0 auto;
  padding: 7px clamp(20px, 3vw, 48px);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 22px;
}
.brand { display: flex; align-items: center; gap: 11px; flex-shrink: 0; }
.mark { color: var(--ink); transition: transform 0.6s var(--ease); }
.brand:hover .mark { transform: rotate(26deg); }
.brand-txt { display: flex; flex-direction: column; line-height: 1.15; }
.brand-name {
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: 0.19em;
  font-weight: 600;
  color: var(--ink);
}
.brand-sub { font-size: 10.5px; color: var(--ink-3); letter-spacing: 0.02em; }

/* 所有章节始终完整显示：宽屏一行，空间不足时按分组换行。 */
.nav {
  display: flex;
  flex: 1 1 760px;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 2px 1px;
  margin-left: auto;
  min-width: 0;
  overflow: visible;
}
.nav-group { display: flex; align-items: center; flex: 0 0 auto; gap: 1px; }
.nav-group + .nav-group { margin-left: 7px; padding-left: 7px; border-left: 1px solid var(--line-2); }
.nav-a {
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
  white-space: nowrap;
  align-items: baseline;
  gap: 5px;
  padding: 7px 6px;
  border-radius: 8px;
  font-size: 12.5px;
  letter-spacing: 0;
  color: var(--ink-3);
  transition: color 0.2s var(--ease), background 0.2s var(--ease);
}
.nav-a:hover { color: var(--ink); background: var(--surface); }
.nav-a.on { color: var(--ink); }
.nav-n { font-family: var(--font-mono); font-size: 8.5px; opacity: 0.5; letter-spacing: 0.03em; }
.nav-a.on .nav-n { opacity: 0.85; color: var(--accent); }
.nav-a::after {
  content: '';
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: 1px;
  height: 1px;
  background: var(--ink);
  transform: scaleX(0);
  transform-origin: left;
  transition: transform 0.34s var(--ease);
  opacity: 0.5;
}
.nav-a.on::after { transform: scaleX(1); }

.hd-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.kbd-btn { height: 32px; padding: 0 10px 0 12px; gap: 8px; font-size: 12px; color: var(--ink-3); }
.kbd-btn:hover { color: var(--ink); }
.kbd-btn kbd { font-size: 9.5px; letter-spacing: 0.04em; margin: 0; }
.icon-btn {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  color: var(--ink-2);
  transition: background 0.2s var(--ease), color 0.2s var(--ease);
}
.icon-btn:hover { background: var(--surface); color: var(--ink); }

.progress {
  position: absolute;
  left: 0;
  bottom: -1px;
  height: 1px;
  width: 100%;
  transform-origin: left;
  background: linear-gradient(90deg, #a98bfa, #3ed2e0, #46d8a0);
  opacity: 0.75;
  transition: transform 0.1s linear;
}

@media (max-width: 1320px) {
  .kbd-btn-t { display: none; }
}
/* 空间不够时先让出品牌文字，章节编号与标题保持完整。 */
@media (max-width: 1200px) {
  .hd-in { gap: 16px; }
  .brand-txt { display: none; }
  .nav { margin: 0 auto; }
  .nav-a { padding: 7px 5px; font-size: 12px; gap: 4px; }
  .nav-n { font-size: 8px; }
  .nav-group + .nav-group { margin-left: 5px; padding-left: 5px; }
}
@media (max-width: 940px) {
  .nav-n { display: none; }
}
@media (max-width: 780px) {
  .hd-in { gap: 10px; padding: 0 16px; }
  .nav { gap: 0; }
  .nav-a { padding: 7px 3px; font-size: 11.5px; gap: 3px; }
  .nav-group + .nav-group { margin-left: 3px; padding-left: 3px; }
}
</style>
