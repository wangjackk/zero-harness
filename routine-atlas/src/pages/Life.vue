<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import LifeTree from '../components/LifeTree.vue'

/* 演示页：?at=0.35 固定某一刻，?seed=23 换一棵。
   路由是 hash 模式，参数跟在 #/life 后面，所以不能只读 location.search。 */
const hash = location.hash
const qs = hash.includes('?') ? hash.slice(hash.indexOf('?') + 1) : location.search.slice(1)
const q = new URLSearchParams(qs)
const at = q.has('at') ? Number(q.get('at')) : undefined
const seed = Number(q.get('seed') ?? 9)

const STAGES = [
  { at: 0.0, t: '种子入土' },
  { at: 0.07, t: '破土' },
  { at: 0.14, t: '抽茎成苗' },
  { at: 0.28, t: '分枝' },
  { at: 0.42, t: '展叶' },
  { at: 0.52, t: '叶上抽梢' },
  { at: 0.62, t: '繁茂' },
  { at: 0.73, t: '转黄' },
  { at: 0.84, t: '落叶' },
  { at: 0.94, t: '枝干断落' }
]

const t = ref(at ?? 0)
/* 一片叶就是一个运行中的实例，所以叶数本身就是这棵树上有几个实例 */
const stat = ref({ leaf: 0, branch: 0, cluster: 0 })
const router = useRouter()
/* 这一页是全屏 Teleport，会盖住站点导航，所以自己留一个出口 */
function back() {
  if (history.length > 1) router.back()
  else router.push('/anatomy')
}
const cur = () => {
  let i = 0
  for (let k = 0; k < STAGES.length; k++) if (t.value >= STAGES[k].at) i = k
  return i
}
</script>

<template>
  <Teleport to="body">
    <div class="wrap">
      <button class="back mono" @click="back">← 返回</button>
      <header class="head">
        <span class="kicker mono">TREE LIFE CYCLE</span>
        <h1>种子、苗、树、枯萎。</h1>
        <p>同一份几何贯穿全程：先以细条破土，再分枝、展叶、由下往上增粗——粗不是照成树比例画好的，是此刻撑住的枝算出来的；<code>stop</code> 一到，叶从梢头先落，枝再从细枝开始一根根从父枝上断开、翻着掉到地上，主干最后灰掉。</p>
        <p class="sub">
          枝干是 <code>create</code> / <code>start</code> 形成的父子关系，叶片是运行中的实例 —— 一片叶一个：这棵树有 <b>{{ stat.leaf }}</b> 片叶、<b>{{ stat.branch }}</b> 条枝。
          叶落光之后枝也一条条断下来——枝同样是 create 出来的实例，stop 之后就不在树上了，所以它落，不是淡掉；主干是根实例自己的身体，没有父枝可断，只灰掉。整棵子树结算完，注册表里的名字不变。
        </p>
        <p class="sub">繁茂之后还有一跳：其中几团的实例又调了一个 routine —— 那团叶就是它的顶芽，梢从团心穿出来把叶顶开，老叶随后落掉，梢头再结一层新叶。新梢挂上去，宿主到主干这条路径也一起粗一点。</p>
      </header>

      <div class="stage">
        <LifeTree :at="at" :seed="seed" @tick="v => (t = v)" @stats="s => (stat = s)" />
      </div>

      <ol class="steps">
        <li v-for="(s, i) in STAGES" :key="s.t" :class="{ on: cur() === i, past: cur() > i }">
          <span class="mono">{{ String(i + 1).padStart(2, '0') }}</span>
          <b>{{ s.t }}</b>
        </li>
      </ol>
    </div>
  </Teleport>
</template>

<style scoped>
.wrap {
  position: fixed; inset: 0; z-index: 60;
  background: radial-gradient(circle at 50% 58%, #13171b 0%, #0a0c0e 60%);
  display: grid; grid-template-rows: auto 1fr auto; align-content: start;
  padding: 40px clamp(20px, 5vw, 72px) 26px;
  font-family: var(--font-sans, system-ui, sans-serif); color: #cfd8dd;
}
.head { max-width: 620px; }
.back {
  position: fixed; top: 26px; right: clamp(20px, 5vw, 72px); z-index: 2;
  padding: 5px 10px; border-radius: 6px;
  font-size: 11px; letter-spacing: .06em; color: #6f7c85;
  border: 1px solid rgba(255, 255, 255, .08); background: rgba(255, 255, 255, .02);
  cursor: pointer; transition: color .2s, border-color .2s;
}
.back:hover { color: #cfe3e8; border-color: rgba(255, 255, 255, .2); }
.kicker { font-size: 9px; letter-spacing: .18em; color: #5e6b74; }
h1 { margin: 8px 0 10px; font-size: clamp(22px, 3vw, 30px); font-weight: 500; letter-spacing: .01em; }
.head p { margin: 0; font-size: 12.5px; line-height: 1.8; color: #8b98a1; }
.head p.sub { margin-top: 7px; color: #7f8c95; }
.head p.sub b { font-weight: 500; color: #cfe3e8; font-family: var(--font-mono, monospace); font-size: 12px; }
.head code { font-family: var(--font-mono, monospace); color: #7fe3ef; font-size: 11.5px; }
.stage { min-height: 0; display: grid; place-items: center; }
/* 高度跟着剩下的那一行走：上面的说明文字一旦变长，树就整体缩一点，
   而不是被 66vh 顶出视口、根部连带土线一起切掉 */
.stage :deep(svg) { width: min(760px, 90vw); height: 100%; max-height: min(66vh, 600px); }
.steps {
  display: grid; grid-template-columns: repeat(10, 1fr);
  margin: 0; padding: 0; list-style: none;
  border-top: 1px solid rgba(255, 255, 255, .07);
}
.steps li { padding: 9px 10px 0; display: grid; gap: 3px; color: #5e6b74; font-size: 10.5px; }
.steps li span { font-size: 8px; letter-spacing: .1em; }
.steps li b { font-weight: 400; }
.steps li.past { color: #7d8a93; }
.steps li.on { color: #dff3f6; }
.steps li.on span { color: #7fe3ef; }
@media (max-width: 760px) {
  .steps { grid-template-columns: repeat(5, 1fr); gap: 8px 0; }
  .head p { font-size: 12px; }
}
</style>
