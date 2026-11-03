<template>
  <Teleport to="body">
    <!-- 树上没有任何界面：没有标题行、没有把手、没有读数。
         它不是一个可以被操纵的窗口，只是留在角落里的一棵树 ——
         开着就长，结束就落，不需要谁去调它的大小，也不需要告诉谁「现在有几个例程」。 -->
    <div class="hud" @mouseleave="hoverId = ''">
      <!-- 点空白处取消选中：这是原来状态行上那个「←」唯一的作用。
           现在树上没有按钮，就把这件事交给树自己 —— 点枝、点叶是选它，
           点到没有东西的地方就是「不看了」。 -->
      <div class="stage" @click="onBlank">
        <svg
          v-if="model.stems.length || scene.departed.length"
          class="tree"
          :viewBox="vb"
          preserveAspectRatio="xMidYMax meet"
        >
          <defs>
            <!-- 全树共用一束侧光（userSpaceOnUse）。每条枝各自按自己的包围盒取色，
                 斜枝上会退化成斜向色块，枝压枝的地方还会露接缝。 -->
            <linearGradient
              :id="`${uid}-bark`" gradientUnits="userSpaceOnUse"
              :x1="model.box.x" y1="0" :x2="model.box.x + model.box.w" y2="0"
            >
              <stop offset="0" stop-color="#1d1913" />
              <stop offset="0.2" stop-color="#332b20" />
              <stop offset="0.46" stop-color="#463a2a" />
              <stop offset="0.72" stop-color="#322a20" />
              <stop offset="1" stop-color="#16130f" />
            </linearGradient>
            <linearGradient
              :id="`${uid}-bark-new`" gradientUnits="userSpaceOnUse"
              :x1="model.box.x" y1="0" :x2="model.box.x + model.box.w" y2="0"
            >
              <stop offset="0" stop-color="#232420" />
              <stop offset="0.36" stop-color="#4c4b40" />
              <stop offset="0.72" stop-color="#3c3b32" />
              <stop offset="1" stop-color="#22231f" />
            </linearGradient>
            <linearGradient
              :id="`${uid}-bark-off`" gradientUnits="userSpaceOnUse"
              :x1="model.box.x" y1="0" :x2="model.box.x + model.box.w" y2="0"
            >
              <stop offset="0" stop-color="#232421" />
              <stop offset="0.4" stop-color="#3b3d38" />
              <stop offset="1" stop-color="#1f201d" />
            </linearGradient>
            <!-- 叶色：三档色还是 /life 那组 GREEN，但每片叶自己从叶柄到叶尖走一遍渐变。
                 整树一束渐变时每片叶只取到一小段，等于一个颜色（所以退回了实色）；
                 纯实色在这个尺寸下是贴纸。objectBoundingBox 的渐变跟着叶自己的
                 transform 一起转，叶朝哪边都是从柄到尖，不会像整树渐变那样错位。
                 柄侧压暗一档：叶有了厚度，才不像贴上去的一块绿。 -->
            <linearGradient
              v-for="g in LEAF_GRADS" :key="g.id" :id="uid + g.id"
              x1="0.5" y1="0" x2="0.52" y2="1"
            >
              <stop offset="0" :stop-color="g.stops[0]" />
              <stop offset="0.52" :stop-color="g.stops[1]" />
              <stop offset="1" :stop-color="g.stops[2]" />
            </linearGradient>
            <mask
              :id="uid" maskUnits="userSpaceOnUse"
              :x="maskBox.x" :y="maskBox.y" :width="maskBox.w" :height="maskBox.h"
            >
              <!-- 只有**正在长**的枝用圆头：那是抽出来的芽尖，没有它枝端是被切平的。
                   未出生时保持平头 —— dash 长度为 0 时圆头会不会留一个点，各浏览器不一致，
                   宁可让还没长出来的枝一个像素都不露 -->
              <path
                v-for="b in barks" :key="b.id"
                :d="b.spine" fill="none" stroke="#fff"
                :stroke-width="Math.max(2.4, b.r0 * 2.3)"
                :stroke-linecap="growP(b) > 0 && growP(b) < 1 ? 'round' : 'butt'"
                pathLength="1" stroke-dasharray="1"
                :stroke-dashoffset="1 - growP(b)"
              />
            </mask>

            <!-- 叶色不用渐变：三档实色（sdk 的 tone()），跟 /life 同一组值。
                 整树一束渐变看着更「高级」，但叶在不同位置取到的颜色几乎一样，
                 shade 就白算了；三档才看得出内膛暗、外周亮。 -->
          </defs>

          <!-- 这里原来有一片 `ellipse`（rx 46% / ry 40%，
               fill rgba(120,150,96,.05)）说是「树冠里从枝缝漏下来的光」。
               它其实是一个几乎铺满画框的整圆：黄绿、5% 不透明，
               压在脸上就是一坨淡黄，而不是光。以前它在暗雾里面不明显；
               暗雾删掉之后它直接对着页面底，露成一圈黄 —— 它从来不是光，
               只是一块比背景亮的圆。
               现在没有底板，也就不存在「密处死黑」需要打亮：
               不画任何整体提亮，明暗只由枝条自己的三档叶色（`tone`）承担。 -->

          <!-- 叶变枝：离开叶集合那一刻的收尾。排在枝干**之前**，
               新枝从它身上穿出来，才读得成「叶张开成枝」；
               排到枝干之后会盖住刚抽出来的那截，看着像叶子挂在枝上 -->
          <g class="turned" style="pointer-events: none">
            <path
              v-for="f in turnedLeaves" :key="f.k"
              :d="LEAF_UNIT" :transform="f.t" :fill="f.fill"
              :opacity="f.op"
            />
          </g>

          <g :mask="`url(#${uid})`">
            <path
              v-for="b in barks" :key="b.id" :d="b.ribbon"
              :fill="`url(#${uid}-bark)`" :opacity="(1 - Math.min(0.22, b.depth * 0.05)).toFixed(2)"
            />
            <!-- 刚 create 还没 start：同一束光换成冷的 -->
            <path
              v-for="b in barks" :key="'n' + b.id"
              v-show="b.state === 'created'"
              :d="b.ribbon" :fill="`url(#${uid}-bark-new)`"
            />
          </g>

          <!-- 选中/悬停：细一道亮线，不改形态 -->
          <path
            v-for="b in barks" :key="'hl' + b.id"
            v-show="b.id === props.selected || b.id === hoverId"
            :d="b.spine" fill="none"
            :stroke="b.id === props.selected ? '#c9a35c' : '#8ea08a'"
            :stroke-width="Math.max(1.1, b.r0 * 0.42)" stroke-linecap="round" opacity="0.6"
          />

          <!-- 枝就是它自己那个 routine（有子实例的那个）。枝和叶是两个不同的 routine：
               枝是父，长在它上面的叶是子。所以两边各有各的名字，互不冒充。
               热区排在叶**之前**：叶与枝的中轴重叠，热区压在上面时叶轮不到 hover。 -->
          <g class="hits">
            <path
              v-for="b in barks" :key="'h' + b.id"
              :d="b.spine" fill="none" stroke="transparent"
              :stroke-width="Math.max(9, b.r0 * 2.4)" stroke-linecap="round"
              @click.stop="emit('select', b.id)"
              @mouseenter="hoverId = b.id"
              @mouseleave="hoverId = ''"
            ><title>{{ b.name }} · 子 {{ b.kids }} · {{ b.state }}</title></path>
          </g>

          <!-- 叶 = 同一条线上挂着的叶片（`blade`）。它不是另一类节点：
               叶柄就是这条线的 `mesh → tip`，所以叶变枝时新枝接着同一点往外长。
               每条线的叶片按自己的进度张开，不整体平移。 -->
          <g class="leaves" @mouseleave="hoverId = ''">
            <line
              v-for="l in allLeaves" :key="'p' + l.id"
              class="petiole"
              :x1="l.mesh.x" :y1="l.mesh.y" :x2="leafV(l).x" :y2="leafV(l).y"
              :opacity="leafV(l).opacity * 0.9"
            />
            <path
              v-for="l in allLeaves" :key="l.id"
              class="leaf"
              :class="{ on: l.id === hoverId || l.id === props.selected }"
              :d="LEAF_UNIT" :transform="leafTransform(l)" :fill="leafFill(l)"
              :opacity="leafV(l).opacity"
              :style="{ pointerEvents: leafV(l).opacity < 0.3 ? 'none' : 'auto' }"
              @mouseenter="hoverId = l.id"
              @click.stop="emit('select', l.id)"
            ><title>{{ l.name }} · {{ l.state }}</title></path>
          </g>

          <!-- 名字：枝的名字在这段枝上，叶的名字在叶尖外侧。一个 routine 一处名字 -->
          <text
            v-for="b in barks" :key="'t' + b.id"
            v-show="b.id === props.selected || b.id === hoverId"
            class="tip" :class="{ on: b.id === props.selected }"
            :x="b.label.x" :y="b.label.y"
          >{{ b.name }}</text>
          <text
            v-for="l in allLeaves" :key="'tl' + l.id"
            v-show="l.id === props.selected || l.id === hoverId"
            class="tip" :class="{ on: l.id === props.selected }"
            :x="l.label.x" :y="l.label.y"
          >{{ l.name }}</text>

          <!-- 结束：这个 routine 不在树上了。
               叶先转黄、离枝、飘落，细枝随后从父枝上断下来 ——
               两者共用同一套解析式（离枝后迅速到终速，之后匀速，横向摆+翻滚），
               主干不落：它是根实例自己的身体，下面没有父枝可断，只灰掉、淡掉。
               这几层排在**叶之后**：早先摆在最底下，落叶一下落就被枝和叶挡住，
               落在树冠里那一截根本看不见，只剩「原地淡出」。
               不吃指针：落下来的叶和枝是残影，不该抢走下面真实节点的 hover。 -->
          <g class="litter" style="pointer-events: none">
            <line
              v-for="d in litter.filter(x => x.petiole)" :key="'p' + d.k"
              class="petiole"
              :x1="d.petiole!.x1" :y1="d.petiole!.y1"
              :x2="d.petiole!.x2" :y2="d.petiole!.y2"
              :opacity="d.petiole!.op"
            />
            <path
              v-for="d in litter" :key="d.k"
              :d="LEAF_UNIT" :transform="d.t" :fill="d.fill" :opacity="d.op"
            />
          </g>
          <g class="fallbark" style="pointer-events: none">
            <g v-for="s in snapped" :key="s.k" :transform="s.t" :opacity="s.op">
              <path v-for="(d, i) in s.paths" :key="i" :d="d" fill="#3a332a" />
            </g>
          </g>
          <g class="dying" style="pointer-events: none">
            <g v-for="s in dying" :key="'d' + s.id">
              <path v-for="(d, i) in s.paths" :key="i" :d="d" fill="#33352f" :opacity="s.op" />
            </g>
          </g>
        </svg>

        <div v-else class="empty">no running routine</div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  TreeRuntime, LEAF_UNIT,
  type GStem, type LeafFrame, type TreeFrame, type TreeInput,
} from '../lib/growth'
/* 明暗档的判据在 sdk 里（tone），叶的位姿口径也是（leafT），别在这里再写一遍 */
import { tone, leafT } from '../lib/treeSdk'
/* 取景是呈现，不是生长：留在 web，但规则是纯函数，脚本能量 */
import { PAD, frameOf, grown, type Box } from '../lib/frame'
import { createLeafFall, leafFallAt, type LeafFall } from '../lib/leafFall'

const props = defineProps<{
  /** 唯一外界入口：一次生命周期事件，或一次全量快照 */
  input: TreeInput | TreeInput[]
  selected?: string | null
}>()

const emit = defineEmits<{ (e: 'select', id: string): void; (e: 'back'): void }>()

/* 同一个页面里可能挂多个实例，mask 与渐变 id 不能撞 */
const uid = 'gh' + Math.random().toString(36).slice(2, 8)

const hoverId = ref('')

/* 点到空白处 = 不看这个节点了。原来是状态行上的「←」干的，
   那一行删掉之后总得留一条退路，否则选中一次就再没有「取消」这个动作。
   枝与叶的点击都是 .stop 的，所以能走到这里的一定没落在节点上。 */
function onBlank() {
  if (props.selected) emit('back')
}

/* ---- 尺寸：固定，不可缩放 ----
   早先这里可以拖动左上角的把手放大、也可以点状态行上的 ⤢ 翻倍，
   尺寸还写进 localStorage。三样都删了：树不是窗口，没有「看它的人定多大」这回事 ——
   一是不该有把手，二是一旦记住过尺寸，下次打开页面树就不是同一棵树了。
   现在宽高只由 CSS 的两个数决定，viewBox 与画法一个像素都不变。 */

/* 树是一个封闭运行时：外界只能从 input 进入，历史和时间推进都在内部。
   这里保存的只是它当前这一帧的只读结果，不再另存出生表或形态历史。 */
const runtime = new TreeRuntime()
const scene = ref<TreeFrame>(runtime.sample())
const model = computed(() => scene.value.model)

/* 一条线画成什么，只由「它头上有没有挂叶片」分：
   挂着的画叶，没挂的画枝干。两个集合是**同一份数据**的两种画法，
   不是两类对象 —— 叶变枝时它只是从这一列走到另一列，id 与几何都没换。 */
const barks = computed(() => model.value.stems.filter(s => !s.blade))
const allLeaves = computed(() => model.value.stems.filter(s => !!s.blade))

/* ---- 画布：画框固定，树长进去 ----
   规则在 `lib/frame.ts`（纯函数，脚本可量）：**只扩不缩**。

   早先是两条规则同时在跑 —— 来新节点就按包围盒扩，静下来 1.2 秒又缩。
   而树的规模在运行中本来就在增删之间来回，于是
   「扩 → 静 1.2s → 缩回来 → 又长大 → 再扩」循环发生，整棵树在呼吸。
   现在只剩一个方向：画框只会变大，所以树只会越来越小一点，不会再突然放大。

   底边恒为 0、横向以 0 对称 —— 主干根部钉在画框底边中央，不随后续节点漂。 */
const frame = ref(frameOf(192))

function growFrame(b: Box) {
  const f = grown(frame.value, b)
  if (f) frame.value = f
}


/* ---- 时间：只读运行时给出的这一帧 ---- */
let raf = 0

function growP(s: GStem): number {
  return scene.value.progress.get(s.id) ?? 0
}

const INVISIBLE_LEAF: LeafFrame = { progress: 0, scale: 0.06, opacity: 0, x: 0, y: 0 }
function leafV(s: GStem): LeafFrame {
  return scene.value.leaves.get(s.id) ?? INVISIBLE_LEAF
}
/** 叶柄与叶片使用同一个当前柄端，不会叶已经贴在梢头、柄却还没长到。 */
function leafTransform(s: GStem): string {
  const b = s.blade
  if (!b) return ''
  const v = leafV(s)
  return leafT({ ...b, x: v.x, y: v.y }, 0, 0, { s: v.scale })
}

/* 叶色照 /life 的 GREEN 三档取，档位由 sdk 的 tone(shade) 定。
   每档再拆成一道从叶尖到叶柄的渐变：叶尖是本档色，柄侧压暗一档。
   平涂在这个尺寸下是一片片贴纸，整树一束渐变又让每片叶只取到一小段（等于一个色），
   只有「跟着叶自己转的渐变」既有档位又有厚度。 */
const GREEN = ['#244328', '#305a2e', '#417136']
/* created 只是刚建、还没跑：压暗偏冷一档就够，不要换成另一个色系 ——
   早先整片叶跳到青绿，一半树是青的，看着像两种植物长在一起 */
const COLD = ['#22382a', '#2c4a34', '#39603f']
const PICK = ['#5e4e21', '#7d6829', '#a08a3a']
const DEEP = '#16281a'
type Ramp = { id: string; stops: [string, string, string] }
const LEAF_GRADS: Ramp[] = []
for (const [k, p] of [['g', GREEN], ['c', COLD], ['p', PICK]] as [string, string[]][]) {
  for (let t = 0; t < 3; t++) {
    LEAF_GRADS.push({ id: '-lf' + k + t, stops: [p[t], p[t], t > 0 ? p[t - 1] : DEEP] })
  }
}
function leafFill(s: GStem): string {
  const k = s.id === props.selected ? 'p' : s.state === 'created' ? 'c' : 'g'
  return `url(#${uid}-lf${k}${tone(s.blade?.shade ?? 0.6)})`
}

/* ---- 结束：叶转黄离枝、细枝断开落下 ----
   落叶不是抛物线。叶子一离枝几乎立刻到终速，之后匀速飘，
   所以用一阶惯性 + 终速的解析式：y(s) = vt·(s − τ(1 − e^(−s/τ)))。
   横向摆、风、翻滚都是 s 的函数，不存状态 —— 也就能在任意帧停住。
   和 /life 里那套是同一个式子（那边一轮 30 秒，这里只有一秒多，
   所以 vt 按「落到地面用多久」反算，而不是抄那边的绝对速度）。 */
const GROUND = 0

function mixHex(a: string, b: string, t: number): string {
  const pa = parseInt(a.slice(1), 16)
  const pb = parseInt(b.slice(1), 16)
  const ch = (s: number) => Math.round(((pa >> s) & 255) + (((pb >> s) & 255) - ((pa >> s) & 255)) * t)
  return `rgb(${ch(16)} ${ch(8)} ${ch(0)})`
}

/** 解一次不动点：s = D/vt + τ(1 − e^(−s/τ)) */
function landTime(drop: number, vt: number, tau: number): number {
  let s = drop / vt + tau
  for (let i = 0; i < 4; i++) s = drop / vt + tau * (1 - Math.exp(-s / tau))
  return s
}

function hashOf(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return ((h >>> 0) % 100000) / 100000
}

/** 落叶与形态转换都从它离开前那一档颜色开始，不在接缝处换色。 */
function toneOf(s: GStem): string {
  const arr = s.state === 'created' ? COLD : GREEN
  return arr[tone(s.blade?.shade ?? 0.6)]
}

interface Drift extends LeafFall {
  /** 离枝前的生长帧，由运行时给出 */
  growth: LeafFrame | null
  from: string
  to: string
}
const drifts = computed<Drift[]>(() => {
  const out: Drift[] = []
  for (const d of scene.value.departed) {
    if (d.kind !== 'leaf' || !d.stem.blade || !d.release) continue
    out.push({
      ...createLeafFall(d.key, d.stem, d.release, d.t0),
      growth: d.growth,
      from: toneOf(d.stem),
      to: '#5f4f2e',
    })
  }
  return out
})

interface Snap {
  k: string
  /** 整段子树一起落：一根枝断了是连着它上面的枝一起掉的，
     一根一根各落一次，会在同一处打转糊成一团黑 */
  paths: string[]
  bx: number
  by: number
  t0: number
  hold: number
  vt: number
  tau: number
  spin: number
  /** 落下时往一侧偏：只在原地转、重心不动的话，读起来是「枝在转」，
     不是「枝断了掉下去」。真断下来的枝条也会往外倒 */
  dx: number
  drop: number
  sLand: number
}
const snaps = computed<Snap[]>(() => {
  const out: Snap[] = []
  for (const d of scene.value.departed) {
    if (d.kind !== 'branch') continue
    const b = d.stem
    const h = hashOf(b.id)
    const drop = Math.max(8, GROUND - b.base.y)
    const vt = drop / (0.5 + h * 0.24)
    const away = b.base.x > 2 ? 1 : b.base.x < -2 ? -1 : h > 0.5 ? 1 : -1
    out.push({
      k: d.key,
      paths: d.group.map(x => x.ribbon),
      bx: b.base.x,
      by: b.base.y,
      t0: d.t0,
      hold: 0.16 + h * 0.1,
      vt,
      tau: 0.09,
      dx: away * (8 + h * 20),
      spin: (h > 0.5 ? 1 : -1) * (34 + h * 52),
      drop,
      sLand: landTime(drop, vt, 0.09),
    })
  }
  return out
})
const trunks = computed(() =>
  scene.value.departed
    .filter(d => d.kind === 'root')
    .map(d => ({ k: d.key, paths: d.group.map(x => x.ribbon), t0: d.t0 }))
)

/* 叶变枝由运行时记录，页面只把这段历史画成连续收拢。
   360ms 内旧叶缩回着生点，新枝同时从同一点抽出，不用突然出现/消失遮接缝。 */
const TURN_FADE = 360
const turnedLeaves = computed(() => {
  const t = scene.value.time
  const out: { k: string; t: string; fill: string; op: number }[] = []
  for (const f of scene.value.turned) {
    const b = f.stem.blade
    if (!b) continue
    const p = Math.min(1, Math.max(0, (t - f.t0) / TURN_FADE))
    const q = p * p * (3 - 2 * p)
    out.push({
      k: f.key,
      t: leafT(b, 0, 0, { s: Math.max(0.06, 1 - q * 0.94) }),
      fill: toneOf(f.stem),
      op: +(1 - q).toFixed(3),
    })
  }
  return out
})

const litter = computed(() => {
  const t = scene.value.time
  const out: {
    k: string
    t: string
    fill: string
    op: number
    petiole: { x1: number; y1: number; x2: number; y2: number; op: number } | null
  }[] = []
  for (const d of drifts.value) {
    const b = d.stem.blade
    if (!b) continue
    const s = (t - d.t0) / 1000
    /* stop 后、离枝前：仍是原来那片叶，沿用运行时的生长帧。
       离枝之后从 release 固定位姿接着走，位置、大小、透明度都不重置。 */
    if (s < 0) {
      const v = d.growth
      if (!v || v.opacity <= 0) continue
      out.push({
        k: d.key,
        t: leafT({ ...b, x: v.x, y: v.y }, 0, 0, { s: v.scale }),
        fill: d.from,
        op: v.opacity,
        petiole: {
          x1: d.stem.mesh.x, y1: d.stem.mesh.y, x2: v.x, y2: v.y, op: v.opacity * 0.9,
        },
      })
      continue
    }
    const pose = leafFallAt(d, t)
    out.push({
      k: d.key,
      t: leafT(
        { ...b, x: pose.x, y: pose.y, rot: pose.rot },
        0,
        0,
        { s: pose.scale }
      ),
      fill: mixHex(d.from, d.to, Math.min(1, s / 0.62)),
      op: pose.opacity,
      /* 转黄停留时叶柄还在；真正离枝后才只画叶片 */
      petiole: pose.released > 0 ? null : {
        x1: d.stem.mesh.x, y1: d.stem.mesh.y,
        x2: d.origin.x, y2: d.origin.y, op: d.origin.opacity * 0.9,
      },
    })
  }
  return out
})

const snapped = computed(() => {
  const t = scene.value.time
  const out: { k: string; paths: string[]; t: string; op: number }[] = []
  for (const s of snaps.value) {
    const sec = (t - s.t0) / 1000
    if (sec <= 0) continue
    const fs = Math.max(0, sec - s.hold)
    const rel = Math.min(fs, s.sLand)
    const y = s.vt * (rel - s.tau * (1 - Math.exp(-rel / s.tau)))
    /* 断口在根部，所以绕自己的基点转，不是绕画布原点；
       同时整段往断口外侧偏出去 —— 只转不移时重心钉在原地，看着像枝在转圈 */
    const land = fs - s.sLand
    out.push({
      k: s.k,
      paths: s.paths,
      t: `translate(${(s.dx * rel).toFixed(1)} ${y.toFixed(1)}) rotate(${(s.spin * rel).toFixed(1)} ${s.bx.toFixed(1)} ${s.by.toFixed(1)})`,
      op: land > 0 ? Math.max(0, 1 - land / 0.6) : 1,
    })
  }
  return out
})

const dying = computed(() =>
  trunks.value.map(s => ({
    id: s.k,
    paths: s.paths,
    op: +Math.max(0, 1 - (scene.value.time - s.t0) / 760).toFixed(2),
  }))
)

/* ---- 唯一入口：吸收一次外部事实，随后只按内部时钟读帧 ---- */
watch(
  () => props.input,
  input => {
    scene.value = runtime.ingest(input)
    growFrame(scene.value.model.box)
    ensureTick()
  },
  { immediate: true }
)

/* 选中项的名字只在树里（hover/选中时浮在枝与叶旁边），
   状态行删掉之后没有第二处要显示它 */
const vb = computed(() => {
  const f = frame.value
  /* 画框本身就是方的，留白已经算进 sideFor，这里不再另加边距 ——
     加一次就是白白缩一圈，树又小一成 */
  return `${f.x.toFixed(1)} ${f.y.toFixed(1)} ${f.w.toFixed(1)} ${f.h.toFixed(1)}`
})
const maskBox = computed(() => {
  const f = frame.value
  return {
    x: (f.x - PAD * 3).toFixed(1),
    y: (f.y - PAD * 3).toFixed(1),
    w: (f.w + PAD * 6).toFixed(1),
    h: (f.h + PAD * 6).toFixed(1),
  }
})

/* 只在有东西在动时才跑 rAF：静止的树不需要每帧重算 */
function ensureTick() {
  if (!raf) raf = requestAnimationFrame(tick)
}

function tick() {
  scene.value = runtime.sample(Date.now())
  raf = scene.value.busy ? requestAnimationFrame(tick) : 0
}

onBeforeUnmount(() => {
  if (raf) cancelAnimationFrame(raf)
  raf = 0
})
</script>

<style scoped>
/* 它不是窗口，是留在角落里的一棵树：没有卡片、没有边框、没有标题行，
   也没有底板 —— 只一片透明的区域，树从页面底色里长出来 */
.hud {
  position: fixed;
  right: 16px;
  bottom: 78px;
  /* 尺寸是写死的，没有缩放：宽高各自只有一个值，viewBox 随画框自己算 */
  width: min(268px, calc(100vw - 32px));
  z-index: 60;
  pointer-events: none;
}

.stage {
  position: relative;
  /* 唯一的两个尺寸之一是 286px（另一个是上面 268px 的宽）：
     没有 --stage-h 这种可变变量，也就没有「拖了但没生效」的状态 */
  height: 286px;
  pointer-events: auto;
}

/* 树底下**没有**底板，也不留暗雾。
   早先这里是 `radial-gradient(70% 66% at 50% 72%, rgba(7,10,8,.94) …, 0 72%)`：
   一块靠羽化假装不存在的圆斑，本意是压住从叶隙里透上来的聊天气泡。
   但它比页面底色（#0f1117）更黑，边缘羽化得再软，在深底上就是一圈暗晕 ——
   树看着像浮在一个看不见的圆盘上。
   现在完全透明。可读性不靠这层底：
   - 叶自带 0.05（叶长单位）的暗边，叶叠叶也分得开；
   - 枝是锥形填充，本身有体积；
   - 屏幕上已经没有别的东西压着树了（读数行也删了），连那层底的用处都没了。 */

.tree {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 100%;
  display: block;
  overflow: visible;
  /* 树干底部淡进暗里：树是从暗处长出来的，不是插在一块板子上。
     没有这层，主干末端就是一条平齐的横口。 */
  -webkit-mask-image: linear-gradient(to top, transparent 0, #000 7%);
  mask-image: linear-gradient(to top, transparent 0, #000 7%);
}

.tip {
  font-family: ui-monospace, "SF Mono", Consolas, monospace;
  /* 尺寸不再可变，字就是字：8.5px 的屏幕像素，不用再按容器反算 */
  font-size: 8.5px;
  fill: #93a091;
  text-anchor: middle;
  paint-order: stroke;
  stroke: rgba(12, 15, 13, 0.9);
  stroke-width: 3px;
  stroke-linejoin: round;
  pointer-events: none;
  user-select: none;
}
.tip.on { fill: #cfa969; }

.hits path { cursor: pointer; }

/* 叶柄：从枝上的着生点连到叶基。
   没有这一截，叶就是一块贴在枝上的绿 —— sdk 那边叶子成群长在枝梢，不需要柄；
   这里一片叶就是一根枝上的一个单元，得看得出来它是「支出去」的。 */
.petiole {
  stroke: #4a3a28;
  stroke-width: 1.1;
  stroke-linecap: round;
  pointer-events: none;
}

/* 悬停/选中一片叶：叶面提亮、浮出一圈极淡的光。
   这里不缩放 —— 叶的 transform 是内联属性，CSS transform 会把它整个替换掉，
   叶会从枝上飞走。
   只亮这一片，不亮同枝的兄弟叶：同一根枝上的叶是不同的 routine，
   整簇一起亮就又把它们讲成一个了。 */
.leaf {
  cursor: pointer;
  transition: filter 0.16s ease;
  /* 一圈极淡的暗边：叶叠叶的时候没有边界就糊成一团绿。
     宽度是叶片自己的单位（叶长 = 1），所以任何尺寸下都是同一个视觉厚度 */
  stroke: rgba(9, 15, 10, 0.5);
  stroke-width: 0.05;
  stroke-linejoin: round;
}
.leaf.on,
.leaf:hover {
  filter: brightness(1.55) saturate(1.16) drop-shadow(0 0 3px rgba(160, 205, 125, 0.55));
}

.empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: #3a423c;
  font-size: 11px;
  font-family: ui-monospace, Consolas, monospace;
}
</style>
