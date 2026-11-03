<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

export interface Relation { dir: 'in' | 'out'; name: string; disp?: string; kind: string; label?: string }
export interface DrawerData {
  name: string
  cls?: string
  band?: string
  accent?: string
  file?: string
  desc?: string
  why?: string
  proc?: string
  ghost?: boolean
  facts?: { k: string; v: string }[]
  relations?: Relation[]
}

const props = defineProps<{ open: boolean; data: DrawerData | null }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'pick', name: string): void }>()

const router = useRouter()
const copied = ref(false)

const out = computed(() => (props.data?.relations || []).filter(r => r.dir === 'out'))
const inc = computed(() => (props.data?.relations || []).filter(r => r.dir === 'in'))

watch(
  () => props.data?.name,
  () => (copied.value = false)
)

async function copy() {
  if (!props.data) return
  try {
    await navigator.clipboard.writeText(props.data.name)
    copied.value = true
    setTimeout(() => (copied.value = false), 1400)
  } catch {
    copied.value = false
  }
}

function toRegistry() {
  if (!props.data) return
  emit('close')
  router.push('/registry?q=' + encodeURIComponent(props.data.name))
}
</script>

<template>
  <Teleport to="body">
    <Transition name="dr">
      <aside v-if="open && data" class="dr" :style="{ '--a': data.accent || '#8A94A6' }" role="dialog" aria-modal="false">
        <div class="dr-head">
          <span class="dr-band mono">
            <i class="dr-dot" />
            {{ data.band || (data.ghost ? '非单元' : '单元') }}
          </span>
          <button class="dr-x" @click="emit('close')" aria-label="关闭">✕</button>
        </div>

        <div class="dr-body">
          <h3 class="dr-name mono">{{ data.name }}</h3>
          <div v-if="data.cls" class="dr-cls mono">{{ data.cls }}</div>
          <p v-if="data.desc" class="dr-desc">{{ data.desc }}</p>

          <blockquote v-if="data.why" class="dr-why">{{ data.why }}</blockquote>

          <div v-if="data.facts?.length" class="dr-facts">
            <div v-for="f in data.facts" :key="f.k" class="dr-fact">
              <span class="dr-fact-k mono">{{ f.k }}</span>
              <span class="dr-fact-v mono">{{ f.v }}</span>
            </div>
          </div>

          <div v-if="data.file" class="dr-file mono">{{ data.file }}</div>

          <div v-if="out.length || inc.length" class="dr-rel">
            <div v-if="out.length" class="dr-rel-g">
              <span class="tag">调用 · {{ out.length }}</span>
              <button v-for="r in out" :key="'o' + r.name + r.kind" class="rel-row" @click="emit('pick', r.name)">
                <span class="rel-k mono">{{ r.kind }}</span>
                <span class="rel-n mono">{{ r.disp || r.name }}</span>
                <span v-if="r.label" class="rel-l">{{ r.label }}</span>
              </button>
            </div>
            <div v-if="inc.length" class="dr-rel-g">
              <span class="tag">被调用 · {{ inc.length }}</span>
              <button v-for="r in inc" :key="'i' + r.name + r.kind" class="rel-row" @click="emit('pick', r.name)">
                <span class="rel-k mono">{{ r.kind }}</span>
                <span class="rel-n mono">{{ r.disp || r.name }}</span>
                <span v-if="r.label" class="rel-l">{{ r.label }}</span>
              </button>
            </div>
          </div>

          <div v-else class="dr-norel dim">当前拓扑未记录该对象的直接关系。</div>
        </div>

        <div class="dr-foot">
          <button class="btn btn-quiet" @click="copy">{{ copied ? '已复制' : '复制注册名' }}</button>
          <button class="btn" @click="toRegistry">查看清单 <span class="arw">→</span></button>
        </div>
      </aside>
    </Transition>
  </Teleport>
</template>

<style scoped>
.dr {
  position: fixed;
  top: calc(var(--nav-h) + 12px);
  right: 12px;
  bottom: 12px;
  width: min(392px, calc(100vw - 24px));
  z-index: 150;
  display: flex;
  flex-direction: column;
  border-radius: 18px;
  border: 1px solid var(--line-2);
  background: color-mix(in oklab, var(--surface-solid) 94%, transparent);
  backdrop-filter: blur(22px) saturate(1.15);
  box-shadow: 0 50px 100px -40px rgba(0, 0, 0, 0.9), 0 1px 0 rgba(255, 255, 255, 0.05) inset;
  overflow: hidden;
}
.dr::before {
  content: '';
  position: absolute;
  inset: 0 0 auto;
  height: 2px;
  background: linear-gradient(90deg, var(--a), transparent 70%);
  opacity: 0.9;
}
.dr-head { display: flex; align-items: center; justify-content: space-between; padding: 16px 18px 0; }
.dr-band {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 9.5px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--ink-3);
}
.dr-dot { width: 6px; height: 6px; border-radius: 99px; background: var(--a); box-shadow: 0 0 12px var(--a); }
.dr-x {
  width: 26px; height: 26px; border-radius: 7px; color: var(--ink-3);
  display: grid; place-items: center; font-size: 12px;
}
.dr-x:hover { background: var(--surface-2); color: var(--ink); }

.dr-body { flex: 1; overflow: auto; padding: 14px 18px 20px; }
.dr-name {
  font-size: 20px;
  letter-spacing: -0.02em;
  font-weight: 500;
  color: var(--ink);
  word-break: break-all;
  line-height: 1.25;
}
.dr-cls { font-size: 11px; color: var(--ink-4); margin-top: 6px; }
.dr-desc { font-size: 13px; line-height: 1.72; color: var(--ink-2); margin-top: 16px; }
.dr-why {
  margin: 16px 0 0;
  padding: 12px 14px;
  border-left: 2px solid var(--a);
  background: color-mix(in oklab, var(--a) 7%, transparent);
  border-radius: 0 8px 8px 0;
  font-size: 12.5px;
  line-height: 1.72;
  color: var(--ink-2);
}
.dr-facts { margin-top: 16px; display: grid; gap: 7px; }
.dr-fact { display: flex; gap: 10px; font-size: 11px; }
.dr-fact-k { color: var(--ink-4); min-width: 62px; letter-spacing: 0.04em; }
.dr-fact-v { color: var(--ink-2); }
.dr-file {
  margin-top: 14px;
  font-size: 10.5px;
  color: var(--ink-3);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 7px 9px;
  word-break: break-all;
}
.dr-rel { margin-top: 22px; display: grid; gap: 18px; }
.dr-rel-g { display: grid; gap: 6px; }
.rel-row {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) auto;
  align-items: baseline;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  text-align: left;
  transition: background 0.2s var(--ease);
}
.rel-row:hover { background: var(--surface-2); }
.rel-k { font-size: 9px; color: var(--ink-4); letter-spacing: 0.06em; text-transform: uppercase; }
.rel-n { font-size: 11.5px; color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; }
.rel-l { font-size: 10.5px; color: var(--ink-4); }
.dr-norel { font-size: 12px; margin-top: 18px; }

.dr-foot {
  display: flex;
  gap: 8px;
  justify-content: space-between;
  padding: 12px 16px;
  border-top: 1px solid var(--line);
  background: color-mix(in oklab, var(--bg-deep) 40%, transparent);
}
.dr-foot .btn { height: 34px; font-size: 12px; }

.dr-enter-active, .dr-leave-active { transition: opacity 0.3s var(--ease), transform 0.34s var(--ease); }
.dr-enter-from, .dr-leave-to { opacity: 0; transform: translateX(18px); }
</style>
