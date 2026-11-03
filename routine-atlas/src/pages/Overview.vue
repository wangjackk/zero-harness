<script setup lang="ts">
import RoutineField from '../components/RoutineField.vue'
import { BANDS } from '../data/bands'
import { TOTAL_NAMES } from '../data/names'
import { CRITERIA } from '../data/criteria'
import { MECHS } from '../data/mechanisms'
import { openPalette } from '../lib/palette'

/* 一个 agent 加它名下的八个工具 —— 读写搜才算基础能力，说话不算 */
const parts = MECHS.map(m => ({
  name: m.name, role: m.role, core: m.kind === 'agent'
}))

/* 首页先阐述共同世界的价值，把注册与生命周期细节留给后章。 */
const wrong = [
  '每增加一种设备或框架，就增加一套封闭的协作方式',
  'Agent、工具与服务彼此隔绝，能力只能停留在各自边界里',
  '每次组合都重新适配，系统越丰富，连接越沉重'
]

const right = [
  '不同主体以同一种单元进入系统，差异不再需要被抹平',
  '能力一旦接入，就能跨越设备、框架与 Agent 被发现和组合',
  '替换实现不改变参与方式，系统可以生长而不必反复重写关系'
]

/* 三种范围共同描述一个世界，而不是上下管辖的控制层级。 */
const LAYERS = [
  {
    k: 'hub', label: '连接', en: 'many', c: '#F0B24A',
    t: '让每一处能力，都能来到同一个世界',
    d: '手机、电脑、机器人和不同运行环境，都可以带着本机能力加入。设备不再只是被封装的终端，而能参与整个系统，让感知与行动自由流动。'
  },
  {
    k: 'kernel', label: '秩序', en: 'one', c: '#A98BFA',
    t: '让不同的能力，共享同一种秩序',
    d: '无论能力来自哪里、由谁实现，都能以同样的方式被发现、调用和结束。系统不必理解每种内部实现，也能让复杂协作保持清晰。'
  },
  {
    k: 'routine', label: '单元', en: 'the only unit', c: '#3ED2E0',
    t: '让不同的存在，都能平等参与',
    d: '一个动作、一段规程、一个 Agent，甚至人的参与，都可以成为单元。它们保留各自的不同，却不再因为身份不同而被排除在组合之外。'
  }
]
</script>

<template>
  <div class="ov">
    <!-- 星系属于整个首页的固定视口背景；只有上方前景随页面滚动。 -->
    <RoutineField class="hero-field" />

    <!-- ============================ HERO ============================ -->
    <section class="hero">
      <div class="page hero-in">
        <div class="hero-top">
          <span class="eyebrow">zero-harness · 能力拓扑 · 2026</span>
          <span class="hero-ver mono">world · execution</span>
        </div>

        <h1 class="display hero-title">
          万物皆<span class="hl"><em>routine</em></span>
        </h1>
        <p class="lede hero-lede">
          <span class="ln">系统由单元构成，秩序在调用中形成。</span>
          <span class="ln">单元随调用而生，随执行而终。</span>
        </p>

        <div class="hero-cta">
          <RouterLink to="/anatomy" class="btn btn-solid">
            从一个单元开始 <span class="arw">→</span>
          </RouterLink>
          <button class="btn" @click="openPalette">
            搜索注册名 <kbd>⌘K</kbd>
          </button>
        </div>
      </div>

      <div class="scroll-hint mono" aria-hidden="true">
        <span>向下</span>
        <i />
      </div>
    </section>

    <!-- ============================ 口径 ============================ -->
    <section id="thesis" class="section thesis">
      <div class="page">
        <div class="sec-head" v-reveal>
          <span class="eyebrow">世界观 · 同一种参与方式</span>
          <h2>世界可以保持复杂，<br />系统不必因此割裂。</h2>
        </div>

        <div class="two-col">
          <div class="col wrong" v-reveal="60">
            <div class="col-h"><span class="x">×</span> 各自为战的能力</div>
            <ul>
              <li v-for="(t, i) in wrong" :key="i">
                <span class="li-n mono">0{{ i + 1 }}</span>
                <span>{{ t }}</span>
              </li>
            </ul>
          </div>

          <div class="col right" v-reveal="140">
            <div class="col-h"><span class="v">✓</span> 共同参与的能力网络</div>
            <ul>
              <li v-for="(t, i) in right" :key="i">
                <span class="li-n mono">0{{ i + 1 }}</span>
                <span>{{ t }}</span>
              </li>
            </ul>
          </div>
        </div>

        <p class="thesis-note" v-reveal="220">
          “万物皆 routine”不是说万物相同，而是说：<b>再不同的存在，也能在同一种秩序中协作。</b>
        </p>
      </div>
    </section>

    <!-- ============================ 三层抽象 ============================ -->
    <section id="layers" class="section layers">
      <div class="page">
        <div class="sec-head" v-reveal>
          <span class="eyebrow">结构 · 三种范围</span>
          <h2>不是层级，<br />是同一个世界的三种尺度。</h2>
          <p class="sec-lede">
            hub 让能力进入，kernel 让协作有序，routine 让万物能够参与。
            它们不彼此管辖，却共同消除异构世界里的边界。
          </p>
        </div>

        <div class="lay">
          <div
            v-for="(l, i) in LAYERS" :key="l.k"
            class="lay-row" v-reveal="i * 70"
            :style="{ '--a': l.c }"
          >
            <div class="lay-spine"><i /></div>
            <div class="lay-k mono">{{ l.k }}</div>
            <div class="lay-b">
              <div class="lay-top">
                <span class="lay-t">{{ l.t }}</span>
                <span class="lay-en mono">{{ l.en }}</span>
              </div>
              <p class="lay-d">{{ l.d }}</p>
            </div>
          </div>
        </div>

        <p class="lay-note" v-reveal="240">
          这三种范围共同带来一种自由：设备可以加入，实现可以替换，能力可以重组，而系统不必随之改变基本形态。
          <RouterLink to="/society">进入异构协作网络</RouterLink>。
        </p>
      </div>
    </section>

    <!-- ============================ 三个概念 ============================ -->
    <section id="domains" class="section domains">
      <div class="page">
        <div class="sec-head" v-reveal>
          <span class="eyebrow">尺度 · 从单元到世界</span>
          <h2>同一个底座，<br />支撑个体，也连接世界。</h2>
          <p class="sec-lede">Routine Core 建立共同秩序，Agent Harness 让个体持续行动，Routine Society 让不同主体共同完成单一个体无法完成的事。</p>
        </div>

        <div class="domain-grid">
          <article class="domain-core" v-reveal="60">
            <span class="domain-en mono">ROUTINE CORE</span>
            <h3>让万物共同执行</h3>
            <p>智能不是入场条件，设备与实现也不是边界。任何能力都能进入同一套秩序，被发现、组合、替换和回收。</p>
            <ul><li>共同秩序</li><li>自由组合</li><li>清晰生命</li></ul>
          </article>
          <article class="domain-agent" v-reveal="120">
            <span class="domain-en mono">AGENT HARNESS</span>
            <h3>让 Agent 持续行动</h3>
            <p>模型不只生成回答，也能感知现场、使用能力、保留经验并响应中断。Harness 把这些支撑组织成完整的行动者。</p>
            <ul><li>感知与决策</li><li>记忆与行动</li><li>中断与恢复</li></ul>
            <RouterLink to="/harness" class="domain-link">进入 Harness 章 <span>→</span></RouterLink>
          </article>
          <article class="domain-society" v-reveal="180">
            <span class="domain-en mono">ROUTINE SOCIETY</span>
            <h3>让协作超越单体边界</h3>
            <p>人、Agent、设备与服务不必变得相同，就能彼此感知、相互调用。能力不再属于某个封闭应用，而能在整个系统中流动。</p>
            <ul><li>异构共存</li><li>能力流动</li><li>开放生长</li></ul>
            <RouterLink to="/society" class="domain-link">进入协作章 <span>→</span></RouterLink>
          </article>
        </div>
        <p class="domain-note" v-reveal="220">从单元到个体，再到系统，变化的是组合尺度，不是基本世界观。</p>
      </div>
    </section>

    <!-- ============================ 七类证据 ============================ -->
    <section id="bands" class="section bands">
      <div class="page">
        <div class="sec-head spread" v-reveal>
          <div>
            <span class="eyebrow">证据 · 七种存在</span>
            <h2>同一种单元，<br />容纳截然不同的能力。</h2>
          </div>
          <div class="sec-meta mono">
            {{ TOTAL_NAMES }} 个单元，分七类<br />
            点击任意一行进入拓扑
          </div>
        </div>

        <div class="band-list">
          <RouterLink
            v-for="(b, i) in BANDS"
            :key="b.id"
            :to="`/topology?band=${b.id}`"
            class="band-row"
            v-reveal="i * 40"
            :style="{ '--a': b.accent }"
          >
            <span class="band-no">{{ b.no }}</span>
            <span class="band-title">{{ b.title }}</span>
            <span class="band-sub">{{ b.sub }}</span>
            <span class="band-names mono">
              <span v-for="c in b.chips.slice(0, 3)" :key="c.n">{{ c.n }}</span>
              <span v-if="b.chips.length > 3" class="more">+{{ b.chips.length - 3 }}</span>
            </span>
            <span class="band-count mono">{{ b.chips.length }}</span>
            <span class="band-arw">→</span>
          </RouterLink>
        </div>

        <p class="bands-foot dim" v-reveal>
          它们可以思考、感知、行动，也可以只执行一段确定的规程。
          统一的不是能力本身，而是能力进入系统、参与协作的方式。
        </p>
      </div>
    </section>

    <!-- ============================ 判据 ============================ -->
    <section id="criteria" class="section criteria">
      <div class="page">
        <div class="sec-head" v-reveal>
          <span class="eyebrow">生命 · 从被发现到结束</span>
          <h2>每个单元，<br />都有完整而清晰的生命。</h2>
        </div>

        <div class="crit-grid">
          <RouterLink v-for="(c, i) in CRITERIA" :key="c.key" to="/anatomy" class="crit" v-reveal="i * 50">
            <div class="crit-top">
              <span class="crit-no mono">{{ c.no }}</span>
              <span class="crit-en mono">{{ c.en }}</span>
            </div>
            <div class="crit-t">{{ c.title }}</div>
            <p class="crit-l">{{ c.line }}</p>
          </RouterLink>
        </div>
      </div>
    </section>

    <!-- ============================ 装配 ============================ -->
    <section id="assembly" class="section assembly">
      <div class="page">
        <div class="sec-head" v-reveal>
          <span class="eyebrow">组合 · 从能力到行动者</span>
          <h2>能力不再只是工具，<br />它们共同构成一个行动者。</h2>
        </div>

        <div class="ag-grid">
          <div class="ag-parts" v-reveal="60">
            <div class="ag-cap mono">一个 agent · 八种行动能力</div>
            <ul>
              <li
                v-for="p in parts" :key="p.name"
                :class="{ hub: p.core }"
                :style="{ '--a': p.core ? '#A98BFA' : '#56C6D6' }"
              >
                <span class="ag-d" />
                <span class="ag-n mono">{{ p.name }}</span>
                <span class="ag-r">{{ p.role }}</span>
              </li>
            </ul>
          </div>

          <div class="ag-why" v-reveal="140">
            <ul>
              <li><b>能力成为身体。</b>读取、检索、写入与执行不再散落于不同接口，而成为 Agent 可以自由使用的行动能力。</li>
              <li><b>组合大于堆叠。</b>模型决定下一步，单元把决定带入现实，结果再回到感知之中。行动因此不是一次回答，而是持续推进的过程。</li>
              <li><b>行动者仍是单元。</b>装配出的 Agent 没有获得特殊身份。它同样可以被人、设备或其他 Agent 调用，成为更大系统的一部分。</li>
            </ul>
            <RouterLink to="/assemble" class="btn btn-solid">
              查看一个 Agent 如何形成 <span class="arw">→</span>
            </RouterLink>
          </div>
        </div>
      </div>
    </section>

    <!-- ============================ 收尾 ============================ -->
    <section id="outro" class="outro">
      <div class="page">
        <div class="outro-in" v-reveal>
          <div>
            <span class="eyebrow">下一步</span>
            <h2 class="outro-h">让一个单元进入世界，<br />让整个系统开始协作。</h2>
          </div>
          <div class="outro-btns">
            <RouterLink to="/topology" class="btn btn-solid">探索能力拓扑 <span class="arw">→</span></RouterLink>
            <RouterLink to="/bootstrap" class="btn">看系统如何生长 <span class="arw">→</span></RouterLink>
          </div>
        </div>      </div>
    </section>
  </div>
</template>

<style scoped>
/* ------------------------------ hero ------------------------------ */
.ov { position: relative; }
/* 固定、统一的轻度遮罩只负责文字对比度；各章节不再分别铺设背景。 */
.ov::before {
  content: '';
  position: fixed;
  z-index: 0;
  inset: var(--nav-h) 0 0;
  background: color-mix(in oklab, var(--bg) 38%, transparent);
  pointer-events: none;
}
.ov > .section, .ov > .outro {
  position: relative;
  z-index: 1;
}
.hero {
  position: relative;
  z-index: 1;
  min-height: calc(100vh - var(--nav-h));
  display: flex;
  flex-direction: column;
  padding: clamp(44px, 9vh, 104px) 0 clamp(28px, 4vh, 48px);
  overflow: hidden;
}
.sky { position: absolute; inset: 0; opacity: 0.95; }
.hero-field {
  position: fixed;
  /* 不设层叠上下文：画布位于前景下，控制按钮仍可置于前景上。 */
  top: var(--nav-h);
  right: 0;
  bottom: 0;
  left: 0;
  width: 100%;
  height: calc(100vh - var(--nav-h));
  height: calc(100dvh - var(--nav-h));
}
.hero-in { position: relative; z-index: auto; width: 100%; display: flex; flex-direction: column; flex: 1; pointer-events: none; }
.hero-top { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.hero-ver { font-size: 10px; color: var(--ink-4); letter-spacing: 0.08em; }
.hero-title { margin-top: clamp(22px, 3.4vw, 40px); }
.hero-title .hl { display: inline-block; margin-left: 0.14em; }
.hero-title em {
  background: linear-gradient(96deg, #cbb8ff 0%, #8fd8ea 46%, #7ff0c0 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  font-weight: 480;
}
html[data-theme='light'] .hero-title em {
  background: linear-gradient(96deg, #6b4ed8, #0e8fa3 55%, #0f9c6d);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.hero-lede { margin-top: clamp(28px, 3.4vw, 42px); max-width: 58ch; font-size: 17.5px; line-height: 2; color: var(--ink); }
.hero-lede .ln { display: block; }
@media (max-width: 740px) { .hero-lede .ln + .ln { margin-top: 0.45em; } }
.hero-cta { display: flex; gap: 10px; margin-top: 30px; flex-wrap: wrap; }
.hero-cta kbd {
  font-size: 9.5px;
  padding: 2px 5px;
  border-radius: 4px;
  border: 1px solid var(--line-2);
  color: var(--ink-3);
  margin-left: 2px;
}

.hero-top, .hero-title, .hero-lede, .hero-cta { position: relative; z-index: 2; pointer-events: auto; }

.scroll-hint {
  position: absolute;
  right: clamp(20px, 5vw, 56px);
  bottom: 20px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 9.5px;
  letter-spacing: 0.2em;
  color: var(--ink-4);
  z-index: 2;
}
.scroll-hint i {
  display: block;
  width: 46px;
  height: 1px;
  background: linear-gradient(90deg, var(--line-3), transparent);
  position: relative;
  overflow: hidden;
}
.scroll-hint i::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--ink-2);
  animation: sweep 2.4s var(--ease) infinite;
}
@keyframes sweep {
  0% { transform: translateX(-100%); }
  60%, 100% { transform: translateX(100%); }
}

/* ------------------------------ 通用段头 ------------------------------ */
.sec-head { margin-bottom: clamp(30px, 4vw, 52px); }
.sec-head h2 { margin-top: 20px; }
.sec-head.spread { display: flex; align-items: flex-end; justify-content: space-between; gap: 30px; flex-wrap: wrap; }
.sec-lede { margin-top: 18px; max-width: 66ch; font-size: 14px; line-height: 1.85; color: var(--ink-3); }
.sec-meta { font-size: 10px; line-height: 1.9; color: var(--ink-4); text-align: right; letter-spacing: 0.04em; }

/* ------------------------------ thesis ------------------------------ */
.thesis { border-top: 1px solid var(--line); }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: clamp(24px, 4vw, 64px); margin-top: 8px; }
.col { border-top: 1px solid var(--line-2); padding-top: 20px; }
.col-h {
  display: flex;
  align-items: center;
  gap: 10px;
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--ink-3);
  margin-bottom: 22px;
}
.col-h .x, .col-h .v {
  width: 18px; height: 18px; display: grid; place-items: center;
  border-radius: 50%; font-size: 10px; border: 1px solid var(--line-2);
}
.wrong .col-h .x { color: var(--ink-4); }
.right .col-h .v { color: #46d8a0; border-color: color-mix(in oklab, #46d8a0 40%, transparent); }
.col ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 16px; }
.col li { display: grid; grid-template-columns: 28px 1fr; gap: 12px; align-items: baseline; font-size: 15px; line-height: 1.6; }
.col .li-n { font-size: 9.5px; color: var(--ink-4); letter-spacing: 0.06em; }
.wrong li { color: var(--ink-3); }
.wrong li span:last-child {
  text-decoration: line-through;
  text-decoration-color: color-mix(in oklab, var(--ink-4) 60%, transparent);
  text-decoration-thickness: 1px;
}
.right li { color: var(--ink); }
.thesis-note {
  margin-top: 44px;
  font-size: clamp(16px, 1.6vw, 19px);
  line-height: 1.7;
  color: var(--ink-2);
  max-width: 74ch;
  border-left: 1px solid var(--line-2);
  padding-left: 20px;
}
.thesis-note b { color: var(--ink); font-weight: 560; }

/* ------------------------------ 三个概念 ------------------------------ */
.domains { border-top: 1px solid var(--line); }
.domain-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: clamp(16px, 2vw, 28px); }
.domain-grid article { padding: clamp(22px, 3vw, 34px); border: 1px solid var(--line-2); border-radius: var(--r-lg); background: var(--surface); }
.domain-core { border-top-color: #3ed2e0 !important; }
.domain-agent { border-top-color: #a98bfa !important; }
.domain-society { border-top-color: #e5b567 !important; }
.domain-en { font-size: 10px; letter-spacing: .12em; color: var(--ink-4); }
.domain-grid h3 { margin-top: 16px; color: var(--ink); font-size: 22px; font-weight: 520; }
.domain-grid p { margin-top: 14px; max-width: 48ch; color: var(--ink-3); font-size: 13.5px; line-height: 1.9; }
.domain-grid ul { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 24px; padding: 0; list-style: none; }
.domain-grid li { padding: 5px 9px; border: 1px solid var(--line); border-radius: 6px; color: var(--ink-2); font-size: 11px; }
.domain-link { display: inline-flex; gap: 8px; margin-top: 22px; color: #b9a3f4; font-size: 12px; }
.domain-link:hover { color: var(--ink); }
.domain-note { margin-top: 26px; padding-left: 18px; border-left: 1px solid var(--line-3); color: var(--ink-2); font-size: 13.5px; line-height: 1.8; }

/* ------------------------------ 七类 ------------------------------ */
.bands { border-top: 1px solid var(--line); }
.band-list { border-top: 1px solid var(--line); }
.band-row {
  position: relative;
  display: grid;
  grid-template-columns: 34px minmax(0, 1.35fr) minmax(0, 1fr) minmax(0, 1.5fr) 34px 26px;
  align-items: center;
  gap: 14px;
  padding: 19px 8px 19px 0;
  border-bottom: 1px solid var(--line);
  transition: background 0.3s var(--ease), padding 0.3s var(--ease);
}
.band-row::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  width: 2px;
  height: 0;
  background: var(--a);
  transform: translateY(-50%);
  transition: height 0.3s var(--ease);
}
.band-row:hover { background: color-mix(in oklab, var(--a) 5%, transparent); padding-left: 10px; }
.band-row:hover::before { height: 62%; }
.band-no { font-size: 15px; color: var(--a); font-weight: 500; }
.band-title { font-size: 16px; letter-spacing: -0.02em; color: var(--ink); }
.band-sub { font-size: 12px; color: var(--ink-3); }
.band-names { display: flex; gap: 6px; flex-wrap: wrap; overflow: hidden; }
.band-names span {
  font-size: 10.5px;
  color: var(--ink-3);
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--surface);
  border: 1px solid var(--line);
  transition: color 0.3s var(--ease), border-color 0.3s var(--ease);
  white-space: nowrap;
}
.band-row:hover .band-names span { color: var(--ink-2); border-color: var(--line-2); }
.band-names .more { background: none; border-style: dashed; }
.band-count { font-size: 10px; color: var(--ink-4); text-align: right; }
.band-arw { font-family: var(--font-mono); font-size: 12px; color: var(--ink-4); transition: transform 0.3s var(--ease), color 0.3s var(--ease); }
.band-row:hover .band-arw { transform: translateX(4px); color: var(--ink-2); }
.bands-foot { margin-top: 22px; font-size: 12.5px; line-height: 1.8; max-width: 76ch; }
.bands-foot code { font-size: 11.5px; color: var(--ink-2); }

/* ------------------------------ 判据 ------------------------------ */
.criteria { border-top: 1px solid var(--line); }
.crit-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 1px; background: var(--line); border: 1px solid var(--line); border-radius: var(--r); overflow: hidden; }
.crit {
  background: var(--bg);
  padding: 20px 18px 22px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: background 0.3s var(--ease);
}
.crit:hover { background: var(--surface); }
/* 顶行预留两行高度：英文标签长短不一，不能让五个标题各自起跳 */
.crit-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; min-height: 27px; }
.crit-no { font-size: 10px; color: var(--ink-4); letter-spacing: 0.06em; }
.crit-en { font-size: 9.5px; line-height: 1.4; color: var(--ink-4); letter-spacing: 0.06em; text-align: right; }
.crit-t { font-size: 17px; letter-spacing: -0.02em; }
.crit-l { font-size: 12.5px; line-height: 1.65; color: var(--ink-3); margin-top: auto; }

/* ------------------------------ 三层抽象 ------------------------------ */
.layers { border-top: 1px solid var(--line); }
.lay { display: grid; border-top: 1px solid var(--line); }
.lay-row {
  display: grid;
  grid-template-columns: 26px 128px minmax(0, 1fr);
  gap: 20px;
  padding: 30px 0;
  border-bottom: 1px solid var(--line);
}
.lay-spine { position: relative; }
.lay-spine i { position: absolute; left: 5px; top: 0; bottom: -31px; width: 1px; background: var(--line-2); }
.lay-row:last-child .lay-spine i { bottom: 52%; }
.lay-spine::after {
  content: '';
  position: absolute; left: 1px; top: 5px;
  width: 9px; height: 9px; border-radius: 50%;
  border: 1px solid var(--a); background: var(--bg);
  box-shadow: 0 0 0 4px color-mix(in oklab, var(--a) 12%, transparent);
}
.lay-k { font-size: 12px; color: var(--ink-2); letter-spacing: 0.02em; padding-top: 2px; }
.lay-top { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.lay-t { font-size: 18px; letter-spacing: -0.015em; }
.lay-en { font-size: 9.5px; color: var(--ink-4); letter-spacing: 0.08em; }
.lay-d { margin-top: 10px; font-size: 13px; line-height: 1.85; color: var(--ink-3); max-width: 640px; }
.lay-note { margin-top: 26px; font-size: 13.5px; color: var(--ink-2); }
.lay-note a { color: var(--ink); border-bottom: 1px solid var(--line-3); }
.lay-note a:hover { border-color: var(--accent); }

/* ------------------------------ 装配 ------------------------------ */
.assembly { border-top: 1px solid var(--line); }
.ag-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.92fr) minmax(0, 1.08fr);
  gap: 28px;
  align-items: start;
}
.ag-parts {
  border: 1px solid var(--line); border-radius: var(--r);
  background: var(--surface); padding: 20px 20px 14px;
}
.ag-cap { font-size: 9.5px; color: var(--ink-4); letter-spacing: 0.1em; margin-bottom: 10px; }
.ag-parts ul { list-style: none; display: grid; gap: 2px; }
.ag-parts li {
  display: grid; grid-template-columns: 8px minmax(0, 1fr) 58px;
  align-items: center; gap: 12px;
  padding: 9px 10px; border-radius: 8px;
  transition: background 0.3s var(--ease);
}
.ag-parts li:hover { background: color-mix(in oklab, var(--a) 8%, transparent); }
.ag-parts li.hub { background: color-mix(in oklab, var(--a) 6%, transparent); }
.ag-parts li.hub .ag-n { color: var(--ink); }
.ag-parts li.hub .ag-r { color: var(--ink-3); }
.ag-d { width: 6px; height: 6px; border-radius: 50%; background: var(--a); box-shadow: 0 0 8px color-mix(in oklab, var(--a) 55%, transparent); }
.ag-n { font-size: 12px; color: var(--ink-2); }
.ag-r { font-size: 11px; color: var(--ink-4); text-align: right; }
.ag-why ul { list-style: none; display: grid; gap: 18px; }
.ag-why li {
  position: relative; padding-left: 18px;
  font-size: 13.5px; line-height: 1.85; color: var(--ink-3);
}
.ag-why li::before { content: ''; position: absolute; left: 0; top: 0.82em; width: 6px; height: 1px; background: var(--line-3); }
.ag-why b { color: var(--ink); font-weight: 550; }
.ag-why .btn { margin-top: 26px; }

/* ------------------------------ 收尾 ------------------------------ */
.outro { padding: clamp(50px, 7vw, 96px) 0 0; }
.outro-in {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 30px;
  flex-wrap: wrap;
  padding: clamp(30px, 4vw, 48px);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background:
    radial-gradient(120% 140% at 0% 0%, color-mix(in oklab, #a98bfa 12%, transparent), transparent 55%),
    radial-gradient(100% 120% at 100% 100%, color-mix(in oklab, #3ed2e0 10%, transparent), transparent 60%),
    var(--surface);
  box-shadow: var(--shadow-2);
}
.outro-h { margin-top: 18px; font-size: clamp(24px, 3vw, 34px); }
.outro-btns { display: flex; gap: 10px; flex-wrap: wrap; }

@media (max-width: 1280px) {
  .hero-lede { max-width: 54ch; }
}
@media (max-width: 1100px) {
  .hero-field { opacity: .68; }
  .hero-lede { max-width: 62ch; }
}
@media (max-width: 1000px) {
  .domain-grid { grid-template-columns: 1fr; }
  .crit-grid { grid-template-columns: repeat(2, 1fr); }
  .band-row { grid-template-columns: 30px minmax(0, 1fr) 80px 26px; }
  .band-sub, .band-names { display: none; }
  .band-count { text-align: right; }
}
@media (max-width: 760px) {
  .two-col, .domain-grid { grid-template-columns: 1fr; gap: 30px; }
  .hero-ver { display: none; }
  .hero-field { opacity: .52; }
  .hero { min-height: 760px; }
  .scroll-hint { display: none; }
}
</style>
