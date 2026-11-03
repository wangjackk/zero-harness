<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ROUTES, ROUTE_GROUPS, type RouteGroup } from '../router'
import { paletteOpen, closePalette } from '../lib/palette'
import { bandOf, BAND_MAP } from '../data/bands'
import { NAMES, TOTAL_NAMES } from '../data/names'

interface Row { kind: 'page' | 'routine'; label: string; sub: string; to: string; accent?: string; group?: RouteGroup }
const groupLabel = (id?: RouteGroup) => ROUTE_GROUPS.find(g => g.id === id)?.label || '页面'

const rows = computed<any[]>(() => NAMES as any[])
const q = ref('')
const cursor = ref(0)
const inputEl = ref<HTMLInputElement | null>(null)

const results = computed<Row[]>(() => {
  const s = q.value.trim().toLowerCase()
  const pages: Row[] = (s ? ROUTES.filter(r => (r.title + r.en + r.blurb).toLowerCase().includes(s)) : ROUTES)
    .map(r => ({ kind: 'page' as const, label: r.title, sub: r.en + ' · ' + r.blurb, to: r.path, group: r.group }))
  const routines: Row[] = s
    ? rows.value
        .filter(r => r.name.toLowerCase().includes(s) || (r.desc || '').toLowerCase().includes(s))
        .slice(0, 8)
        .map(r => {
          const b = bandOf(r.name)
          return {
            kind: 'routine' as const,
            label: r.name,
            sub: r.cls + (r.desc ? ' · ' + r.desc.slice(0, 46) : ''),
            to: '/registry?q=' + encodeURIComponent(r.name),
            accent: b === 'other' ? '#8A94A6' : BAND_MAP[b].accent
          }
        })
    : rows.value.slice(0, 5).map(r => {
        const b = bandOf(r.name)
        return {
          kind: 'routine' as const,
          label: r.name,
          sub: r.cls,
          to: '/registry?q=' + encodeURIComponent(r.name),
          accent: b === 'other' ? '#8A94A6' : BAND_MAP[b].accent
        }
      })
  return [...pages, ...routines]
})

const router = useRouter()

watch(paletteOpen, async v => {
  if (v) {
    q.value = ''
    cursor.value = 0
    await nextTick()
    inputEl.value?.focus()
  }
})
watch(q, () => (cursor.value = 0))

function go(i: number) {
  const r = results.value[i]
  if (!r) return
  closePalette()
  router.push(r.to)
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') return closePalette()
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    cursor.value = (cursor.value + 1) % Math.max(results.value.length, 1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    cursor.value = (cursor.value - 1 + results.value.length) % Math.max(results.value.length, 1)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    go(cursor.value)
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="pal">
      <div v-if="paletteOpen" class="pal-wrap" @click.self="closePalette">
        <div class="pal" role="dialog" aria-modal="true" aria-label="跳转与搜索">
          <div class="pal-input">
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <circle cx="7" cy="7" r="4.6" stroke="currentColor" stroke-width="1.3" />
              <path d="M10.6 10.6 14 14" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
            </svg>
            <input
              ref="inputEl"
              v-model="q"
              type="text"
              :placeholder="`跳转页面，或搜索 ${TOTAL_NAMES} 个单元…`"
              spellcheck="false"
              @keydown="onKey"
            />
            <kbd>ESC</kbd>
          </div>
          <div class="pal-list">
            <template v-for="(r, i) in results" :key="r.kind + r.to + r.label">
              <div v-if="r.kind === 'page' && (i === 0 || results[i - 1].kind === 'routine' || results[i - 1].group !== r.group)" class="pal-cap">
                {{ groupLabel(r.group) }}
              </div>
              <div v-if="r.kind === 'routine' && (i === 0 || results[i - 1].kind === 'page')" class="pal-cap">
                单元
              </div>
              <button
                class="pal-row"
                :class="{ on: i === cursor }"
                @mouseenter="cursor = i"
                @click="go(i)"
              >
                <span v-if="r.accent" class="pal-dot" :style="{ background: r.accent }" />
                <span v-else class="pal-no">{{ ROUTES.find(p => p.path === r.to)?.no }}</span>
                <span class="pal-label" :class="{ mono: r.kind === 'routine' }">{{ r.label }}</span>
                <span class="pal-sub">{{ r.sub }}</span>
                <span class="pal-enter" v-if="i === cursor">↵</span>
              </button>
            </template>
            <div v-if="!results.length" class="pal-empty">无匹配结果。请修改关键词，或按 ESC 关闭。</div>
          </div>
          <div class="pal-foot">
            <span><kbd>↑</kbd><kbd>↓</kbd> 移动</span>
            <span><kbd>↵</kbd> 打开</span>
            <span class="pal-foot-r">{{ TOTAL_NAMES }} 个单元 · {{ ROUTES.length }} 个页面</span>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.pal-wrap {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: grid;
  place-items: start center;
  padding: clamp(56px, 12vh, 140px) 20px 20px;
  background: rgba(3, 4, 7, 0.62);
  backdrop-filter: blur(10px) saturate(1.1);
}
html[data-theme='light'] .pal-wrap { background: rgba(24, 28, 36, 0.34); }
.pal {
  width: min(680px, 100%);
  border-radius: 18px;
  border: 1px solid var(--line-2);
  background: color-mix(in oklab, var(--surface-solid) 92%, transparent);
  box-shadow: 0 60px 120px -50px rgba(0, 0, 0, 0.9);
  overflow: hidden;
  backdrop-filter: blur(20px);
}
.pal-input {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 18px;
  border-bottom: 1px solid var(--line);
  color: var(--ink-3);
}
.pal-input input {
  flex: 1;
  border: 0;
  background: none;
  outline: none;
  color: var(--ink);
  font-size: 15px;
  letter-spacing: -0.01em;
}
.pal-input input::placeholder { color: var(--ink-3); }
kbd {
  font-size: 10px;
  padding: 2px 5px;
  border-radius: 4px;
  border: 1px solid var(--line-2);
  color: var(--ink-3);
  background: var(--surface);
  margin-left: 3px;
}
.pal-list { max-height: min(52vh, 440px); overflow: auto; padding: 8px; }
.pal-cap {
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--ink-4);
  padding: 10px 10px 6px;
}
.pal-row {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr) minmax(0, 1.15fr) 18px;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 10px;
  border-radius: 9px;
  text-align: left;
  color: var(--ink-2);
}
.pal-row.on { background: var(--surface-2); color: var(--ink); }
.pal-dot { width: 6px; height: 6px; border-radius: 99px; margin-left: 6px; }
.pal-no { font-family: var(--font-mono); font-size: 10px; color: var(--ink-4); padding-left: 4px; }
.pal-label { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pal-label.mono { font-family: var(--font-mono); font-size: 12.5px; }
.pal-sub {
  font-size: 11.5px;
  color: var(--ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pal-enter { font-family: var(--font-mono); color: var(--ink-3); font-size: 12px; }
.pal-empty { padding: 26px 12px; color: var(--ink-3); font-size: 13px; }
.pal-foot {
  display: flex;
  gap: 16px;
  align-items: center;
  padding: 10px 16px;
  border-top: 1px solid var(--line);
  font-size: 11px;
  color: var(--ink-3);
}
.pal-foot-r { margin-left: auto; font-family: var(--font-mono); font-size: 10px; letter-spacing: 0.06em; }
.pal-enter-active, .pal-leave-active { transition: opacity 0.22s var(--ease); }
.pal-enter-active .pal, .pal-leave-active .pal { transition: transform 0.26s var(--ease); }
.pal-enter-from, .pal-leave-to { opacity: 0; }
.pal-enter-from .pal, .pal-leave-to .pal { transform: translateY(-8px) scale(0.99); }

@media (max-width: 640px) {
  .pal-row { grid-template-columns: 20px minmax(0, 1fr) 16px; }
  .pal-sub { display: none; }
}
</style>
