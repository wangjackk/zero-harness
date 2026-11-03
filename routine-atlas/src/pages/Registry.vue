<script setup lang="ts">
import { pageNo } from '../router'
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NAMES, TOTAL_NAMES, NAMES_PY, NAMES_TS, NAMES_RS } from '../data/names'
import { BANDS, BAND_MAP, bandOf, type BandId } from '../data/bands'
import RoutineDrawer, { type DrawerData } from '../components/RoutineDrawer.vue'

interface Row {
  name: string
  cls: string
  file: string
  desc: string
  passive: boolean
  input: boolean
  inZero: boolean
  inMoss: boolean
}
const rows = NAMES as unknown as Row[]

const route = useRoute()
const q = ref(String(route.query.q || ''))
const onlyPassive = ref(false)
const sort = ref<'name' | 'file'>('name')
const drawer = ref(false)
const sel = ref<Row | null>(null)

const filtered = computed(() => {
  const s = q.value.trim().toLowerCase()
  return rows.filter(r => {
    if (onlyPassive.value && !r.passive) return false
    if (s && !(r.name.toLowerCase().includes(s) || (r.desc || '').toLowerCase().includes(s) || r.cls.toLowerCase().includes(s))) return false
    return true
  })
})

const sorted = computed(() => {
  const arr = [...filtered.value]
  arr.sort((a, b) => (sort.value === 'name' ? a.name.localeCompare(b.name) : a.file.localeCompare(b.file) || a.name.localeCompare(b.name)))
  return arr
})

const groups = computed(() => {
  const out = BANDS.map(b => ({
    id: b.id as BandId,
    no: b.no,
    title: b.title,
    accent: b.accent,
    rows: sorted.value.filter(r => bandOf(r.name) === b.id)
  }))
  const other = sorted.value.filter(r => bandOf(r.name) === 'other')
  if (other.length) out.push({ id: 'other' as any, no: '·', title: '其他', accent: '#8A94A6', rows: other })
  return out.filter(g => g.rows.length)
})

const passiveCount = computed(() => rows.filter(r => r.passive).length)

function open(r: Row) {
  sel.value = r
  drawer.value = true
}

const drawerData = computed<DrawerData | null>(() => {
  const r = sel.value
  if (!r) return null
  const b = bandOf(r.name)
  const band = b === 'other' ? null : BAND_MAP[b]
  return {
    name: r.name,
    cls: r.cls,
    band: band ? `${band.no} ${band.title}` : '未归类',
    accent: band?.accent || '#8A94A6',
    desc: r.desc,
    file: r.file,
    facts: [
      { k: '驻留', v: r.passive ? 'passive：kernel 启动' : '按调用创建命令实例' },
      { k: '入参', v: r.input ? '有 input_schema' : '无参数' },
      { k: '调用', v: `run(name='${r.name}', kwargs)` }
    ]
  }
})

const searchEl = ref<HTMLInputElement | null>(null)
function syncUrl() {
  const url = new URL(location.href)
  if (q.value) url.searchParams.set('q', q.value)
  else url.searchParams.delete('q')
  history.replaceState(null, '', url.toString())
}
watch(q, syncUrl)
</script>

<template>
  <div class="rg">
    <section class="page page-head">
      <div class="kicker">
        <span>清单 · Registry</span>
        <span class="dim">{{ pageNo('/registry') }}</span>
      </div>
      <h1>{{ TOTAL_NAMES }} 个注册名，<br />完整清单。</h1>
      <p class="lede">
        本表直接展示 <code>load_from_yaml</code> 的返回值，共 <b>{{ TOTAL_NAMES }}</b> 个注册项：{{ NAMES_PY }} 个由 py 注册，{{ NAMES_TS }} 个经 ts 的 hub 注册，{{ NAMES_RS }} 个经 rust 的 hub 注册。
        其中 <b>{{ passiveCount }}</b> 个为 passive 常驻单元，负责输入、总线、执行和管理；其余按调用创建，执行结束后销毁。
        选择任意一行可查看该注册项的声明，内容直接来自注册数据。
      </p>
    </section>

    <!-- ------------------------------ 工具条 ------------------------------ -->
    <div class="tools">
      <div class="tools-in page">
        <div class="sbox">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <circle cx="7" cy="7" r="4.6" stroke="currentColor" stroke-width="1.4" />
            <path d="M10.6 10.6 14 14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
          </svg>
          <input ref="searchEl" v-model="q" type="text" placeholder="按注册名 / 说明 / 类名搜索…" spellcheck="false" />
          <button v-if="q" @click="q = ''" aria-label="清空">✕</button>
        </div>

        <button class="tgl" :class="{ on: onlyPassive }" @click="onlyPassive = !onlyPassive">
          <i />仅 passive
        </button>

        <button class="tgl" @click="sort = sort === 'name' ? 'file' : 'name'">
          排序 · {{ sort === 'name' ? '注册名' : '文件' }}
        </button>

        <span class="count mono">{{ sorted.length }} / {{ rows.length }}</span>
      </div>
    </div>

    <!-- ------------------------------ 表 ------------------------------ -->
    <section class="page table-wrap">
      <div v-for="g in groups" :key="g.id" class="grp">
        <div class="grp-head" :style="{ '--a': g.accent }">
          <span class="grp-no">{{ g.no }}</span>
          <span class="grp-t">{{ g.title }}</span>
          <span class="grp-n mono">{{ g.rows.length }}</span>
        </div>

        <div class="rows">
          <button v-for="r in g.rows" :key="r.name + r.cls" class="row" @click="open(r)">
            <span class="c-name">
              <i class="dot" :style="{ background: g.accent }" />
              <span class="nm mono">{{ r.name }}</span>
            </span>
            <span class="c-cls mono">{{ r.cls }}</span>
            <span class="c-proc">
              <span v-if="r.passive" class="pp passive">passive</span>
              <span v-else class="pp lazy">按需</span>
            </span>
            <span class="c-desc">{{ r.desc || '（未提供 description）' }}</span>
            <span class="c-file mono">{{ r.file }}</span>
          </button>
        </div>
      </div>

      <div v-if="!groups.length" class="empty">
        <span class="mono">0 项匹配</span>
        <p>请修改搜索条件，或按 Ctrl K 搜索全部 {{ TOTAL_NAMES }} 个注册名。</p>
      </div>
    </section>

    <footer class="page provenance">
      <div>
        <span class="tag">当前 zero-harness 应用 · 数据出处</span>
        <p>以下来源描述本次业务装配与文档快照，不构成 Routine Core 的固定组成。</p>
      </div>
      <ul>
        <li><code>app/routines.yaml</code> · loader 实际加载</li>
        <li><code>app/routines/loader.py</code> · 引导流程</li>
        <li><code>src/data/registry.json</code> · py 侧导出</li>
        <li><code>src/data/names.ts</code> · ts / rust 注册名</li>
        <li><code>docs/routine-topology.html</code> · 初版</li>
      </ul>
    </footer>

    <RoutineDrawer :open="drawer" :data="drawerData" @close="drawer = false" />
  </div>
</template>

<style scoped>
.rg { padding-bottom: 40px; }
.page-head h1 { margin-top: 16px; }
.page-head .lede { margin-top: 22px; }
.page-head code { font-size: 13px; color: var(--ink-2); }
.page-head b { color: var(--ink); }

/* ------------------------------ 工具条 ------------------------------ */
.tools {
  position: sticky;
  top: var(--nav-h);
  z-index: 60;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  background: color-mix(in oklab, var(--bg) 86%, transparent);
  backdrop-filter: blur(14px) saturate(1.1);
  margin-top: 12px;
}
.tools-in {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 60px;
  flex-wrap: wrap;
}
.sbox {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--line-2);
  background: var(--surface);
  color: var(--ink-3);
  width: min(320px, 100%);
}
.sbox input { flex: 1; border: 0; background: none; outline: none; color: var(--ink); font-size: 12.5px; min-width: 40px; }
.sbox button { color: var(--ink-3); font-size: 10px; }

.seg { display: flex; gap: 2px; padding: 3px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); }
.seg button { height: 26px; padding: 0 10px; border-radius: 6px; font-size: 11.5px; color: var(--ink-3); }
.seg button:hover { color: var(--ink); }
.seg button.on { background: var(--surface-2); color: var(--ink); }

.tgl {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 11px;
  border-radius: 8px;
  border: 1px solid var(--line);
  font-size: 11.5px;
  color: var(--ink-3);
  transition: color 0.2s var(--ease), border-color 0.2s var(--ease), background 0.2s var(--ease);
}
.tgl:hover { color: var(--ink-2); border-color: var(--line-2); }
.tgl i { width: 6px; height: 6px; border-radius: 99px; border: 1px solid var(--ink-4); }
.tgl.on { color: var(--ink); border-color: color-mix(in oklab, #a98bfa 45%, transparent); background: color-mix(in oklab, #a98bfa 10%, transparent); }
.tgl.on i { background: #a98bfa; border-color: #a98bfa; }
.count { margin-left: auto; font-size: 10px; color: var(--ink-4); letter-spacing: 0.06em; }

/* ------------------------------ 表 ------------------------------ */
.table-wrap { padding-top: 10px; }
.grp { margin-top: 18px; }
.grp-head {
  position: sticky;
  top: calc(var(--nav-h) + 60px);
  z-index: 40;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 4px 9px;
  background: color-mix(in oklab, var(--bg) 92%, transparent);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line);
}
.grp-no { font-size: 12px; color: var(--a); }
.grp-t { font-size: 12px; color: var(--ink-2); letter-spacing: -0.01em; }
.grp-n { margin-left: auto; font-size: 9.5px; color: var(--ink-4); }

.rows { display: grid; }
.row {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr) 118px minmax(0, 1.9fr) minmax(0, 1.15fr);
  align-items: center;
  gap: 14px;
  padding: 9px 6px;
  text-align: left;
  border-bottom: 1px solid color-mix(in oklab, var(--line) 60%, transparent);
  transition: background 0.16s var(--ease);
}
.row:hover { background: var(--surface); }
.c-name { display: flex; align-items: center; gap: 9px; min-width: 0; }
.dot { width: 5px; height: 5px; border-radius: 99px; flex-shrink: 0; }
.nm {
  font-size: 12.2px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color 0.16s var(--ease);
}
.row:hover .nm { color: var(--ink); }
.c-cls { font-size: 10.5px; color: var(--ink-4); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.c-proc { display: flex; gap: 5px; flex-wrap: wrap; }
.pp {
  font-family: var(--font-mono);
  font-size: 8.5px;
  letter-spacing: 0.04em;
  padding: 1.5px 5px;
  border-radius: 4px;
  border: 1px solid var(--line);
  color: var(--ink-3);
}
.pp.lazy { color: var(--ink-4); border-color: var(--line); }
.pp.passive { color: #f0b24a; border-color: color-mix(in oklab, #f0b24a 30%, transparent); }
.c-desc {
  font-size: 12px;
  color: var(--ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.c-file { font-size: 10px; color: var(--ink-4); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.empty { padding: 60px 0; display: grid; gap: 10px; }
.empty span { font-size: 11px; color: var(--ink-2); letter-spacing: 0.08em; }
.empty p { font-size: 13px; color: var(--ink-3); }

.provenance { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr); gap: 32px; margin-top: 46px; padding-top: 22px; border-top: 1px solid var(--line); }
.provenance p { margin-top: 10px; max-width: 46ch; color: var(--ink-4); font-size: 11.5px; line-height: 1.75; }
.provenance ul { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.provenance li { color: var(--ink-4); font-size: 11px; line-height: 1.65; }
.provenance code { color: var(--ink-3); font-size: 10.5px; }

@media (max-width: 1000px) {
  .row { grid-template-columns: minmax(0, 1.2fr) 110px minmax(0, 1.6fr); }
  .c-cls, .c-file { display: none; }
}
@media (max-width: 700px) {
  .provenance { grid-template-columns: 1fr; gap: 20px; }
  .tools-in { height: auto; padding-top: 10px; padding-bottom: 10px; }
  .row { grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr); gap: 8px; }
  .c-proc { display: none; }
  .count { margin-left: 0; }
}
</style>
