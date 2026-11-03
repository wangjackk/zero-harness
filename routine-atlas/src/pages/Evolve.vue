<script setup lang="ts">
/* 本章讨论社会系统中的编排：经验、文本与模型是三种可共存的形式。
   人、规程、工具与 agent 都是单元；不同实现可以在同一调用树中混合嵌套。 */
import Evolution from '../components/Evolution.vue'
import DagRoutine from '../components/DagRoutine.vue'
import StructureRoutine from '../components/StructureRoutine.vue'

const STAGES = [
  { no: '01', k: '经验编排', at: '人 · routine', w: '人依据经验与判断调用其他单元；编排存在于执行者之中' },
  { no: '02', k: '规程编排', at: '文本 · routine', w: '调用次序获得明确的文本形式；执行者依照规程调用其他单元' },
  { no: '03', k: '模型编排', at: 'agent · routine', w: '模型依据目标与上下文决定后续调用；agent 与工具没有抽象特权之分' },
  { no: '04', k: '混合编排', at: '手工 · 自动 · 智能', w: '人、规程与 agent 可以互为调用环节；不同实现类型在同一棵父子实例树中组合' }
]
</script>

<template>
  <div class="ev-p">
    <div class="page">
      <header class="evp-head">
        <span class="eyebrow">演进 · 编排的承担者</span>
        <h1 class="evp-h">
          单元始终存在，<br />
          <em class="eh-c">编排不断获得新的形式</em>
        </h1>
        <p class="evp-lede">
          从社会系统看，人、规程、工具与 agent 都可以抽象为单元。
          经验、文本与模型代表不同的编排方式，<strong>并不构成相互淘汰的线性阶段</strong>。
          它们可以作为不同类型的单元互相调用，把手工确认、自动执行与智能判断组合在同一棵父子实例树中。
        </p>
      </header>

      <Evolution />

      <div class="evp-eras">
        <div v-for="stage in STAGES" :key="stage.no" class="evp-era">
          <div class="evp-top">
            <span class="evp-no mono">{{ stage.no }}</span>
            <b class="evp-k">{{ stage.k }}</b>
          </div>
          <span class="evp-at mono">{{ stage.at }}</span>
          <span class="evp-w">{{ stage.w }}</span>
        </div>
      </div>

      <section class="evp-role" aria-labelledby="role-title">
        <div class="evp-role-copy">
          <span class="eyebrow">调用关系 · 编排器的位置</span>
          <h2 id="role-title">编排器不是一类特殊单元</h2>
          <p>
            一个 routine 只要在执行中调用另一个 routine，就在这次调用中承担编排。
            它同时也可以被上层调用。编排描述的是关系位置，不是实现类型或能力等级。
          </p>
          <p class="evp-role-examples">
            <span>同样成立</span>
            person · sop · agent · DAG · behavior tree · state machine · …
          </p>
        </div>
        <svg viewBox="0 0 620 230" class="evp-role-svg" role="img" aria-label="上层 routine 调用一个 routine，这个 routine 再调用其他 routine，因此同时处于被调用者与编排者的位置">
          <defs><marker id="role-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" class="role-arrow" /></marker></defs>
          <path d="M 142 115 H 224 M 366 103 L 454 62 M 366 127 L 454 168" class="role-edge" marker-end="url(#role-arrow)" />
          <text x="183" y="102" text-anchor="middle" class="role-edge-label">被调用</text>
          <text x="410" y="70" text-anchor="middle" class="role-edge-label">调用</text>
          <text x="410" y="157" text-anchor="middle" class="role-edge-label">调用</text>
          <g transform="translate(75 115)" class="role-node">
            <rect x="-62" y="-25" width="124" height="50" rx="10" class="role-body" />
            <text y="-3" text-anchor="middle" class="role-name">routine</text>
            <text y="14" text-anchor="middle" class="role-sub">上层调用者</text>
          </g>
          <g transform="translate(295 115)" class="role-node current">
            <rect x="-70" y="-32" width="140" height="64" rx="10" class="role-body" />
            <text y="-8" text-anchor="middle" class="role-name">routine</text>
            <text y="11" text-anchor="middle" class="role-sub">被调用者 · 编排者</text>
          </g>
          <g v-for="node in [{n:'routine A',y:50},{n:'routine B',y:180}]" :key="node.n" :transform="`translate(520 ${node.y})`" class="role-node">
            <rect x="-62" y="-25" width="124" height="50" rx="10" class="role-body" />
            <text text-anchor="middle" dominant-baseline="central" class="role-name">{{ node.n }}</text>
          </g>
        </svg>
      </section>

      <section class="evp-composition" aria-labelledby="composition-title">
        <header class="evp-composition-head">
          <span class="eyebrow">组合能力 · 实现与结构示意</span>
          <h2 id="composition-title">内部结构不同，外部调用不变</h2>
          <p>
            routine 不需要为 DAG、行为树或状态机另设系统类型。把结构封装在实现内部，
            外部仍然只是一次 routine 调用。下面以仓库中的 <span class="mono">RunDag</span> 为例，
            展示 DAG 如何作为 routine 执行，并再次作为节点进入其他 DAG。
          </p>
        </header>
        <DagRoutine />
        <div class="evp-structure-intro">
          <h3>换一个内部结构，仍然只需一次调用</h3>
          <p>以下两段仅说明组合方式，不表示仓库已有对应实现。行为树与状态机也不会成为特殊单元：结构整体可以作为 routine 被调用，内部再调用普通 routine。</p>
        </div>
        <div class="evp-structure-grid">
          <StructureRoutine kind="behavior" />
          <StructureRoutine kind="state" />
        </div>
      </section>

      <p class="evp-note">
        四个阶段使用同一条调用语义：<b>单元调用单元</b>。前三态分别观察三种编排方式，
        第四态将它们重新组合：<b>人可以调用 agent，agent 也可以在确认环节调用人</b>；
        规程与其他单元同样参与嵌套。父子关系属于运行中的命令实例，父实例停止时，后代实例随之停止。
      </p>
    </div>
  </div>
</template>

<style scoped>
.ev-p { padding: 96px 0 110px; }
.ev-p .page { max-width: 1320px; }
.evp-head { max-width: 900px; margin-bottom: 30px; }
.evp-h { font-size: clamp(28px,3.2vw,42px); line-height: 1.22; letter-spacing: -.02em; margin: 14px 0 16px; font-weight: 600; }
.evp-h em { font-style: normal; color: var(--accent); }
.evp-lede { font-size: 14px; line-height: 1.95; color: var(--ink-3); max-width: 780px; }
.evp-lede strong { color: var(--ink-2); font-weight: 500; }
.evp-eras { margin-top: 26px; display: grid; grid-template-columns: repeat(4,1fr); border-top: 1px solid var(--line); }
.evp-era { padding: 16px 20px 4px 0; border-right: 1px solid var(--line); display: flex; flex-direction: column; gap: 7px; }
.evp-era:last-child { border-right: none; }
.evp-era:not(:first-child) { padding-left: 20px; }
.evp-top { display: flex; align-items: baseline; gap: 9px; }
.evp-no { font-size: 10px; letter-spacing: .14em; color: var(--accent); opacity: .8; }
.evp-k { font-size: 14.5px; font-weight: 500; color: var(--ink); }
.evp-at { font-size: 11px; color: #0d8492; }
html[data-theme='dark'] .evp-at { color: #7FE3EF; }
.evp-w { font-size: 12.5px; line-height: 1.75; color: var(--ink-4); }
.evp-note { margin: 20px 0 0; font-size: 12px; line-height: 1.9; color: var(--ink-4); }
.evp-note b { color: var(--ink-2); font-weight: 500; }
.evp-role { margin-top: 46px; padding: 30px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); display: grid; grid-template-columns: minmax(280px,.8fr) minmax(460px,1.2fr); align-items: center; gap: 38px; }
.evp-role-copy h2 { margin: 12px 0 10px; font-size: 25px; font-weight: 550; letter-spacing: -.015em; }
.evp-role-copy > p { margin: 0; color: var(--ink-3); font-size: 13px; line-height: 1.85; }
.evp-role-copy .evp-role-examples { margin-top: 17px; color: var(--ink-4); font-family: var(--font-mono); font-size: 10px; line-height: 1.8; }
.evp-role-examples span { display: block; margin-bottom: 4px; color: var(--accent); font-family: var(--font-sans); font-size: 10px; }
.evp-role-svg { display: block; width: 100%; height: auto; }
.role-edge { fill: none; stroke: color-mix(in oklab,var(--accent) 65%,var(--ink-4)); stroke-width: 1.1; }
.role-arrow { fill: var(--accent); }
.role-edge-label { fill: var(--ink-4); font-size: 9px; }
.role-body { fill: var(--surface); stroke: var(--line-2); stroke-width: 1; }
.role-node.current .role-body { fill: color-mix(in oklab,var(--accent) 5%,var(--surface)); stroke: color-mix(in oklab,var(--accent) 55%,var(--line)); }
.role-name { fill: var(--ink-2); font-family: var(--font-mono); font-size: 11px; }
.role-sub { fill: var(--ink-4); font-size: 9px; }
.evp-composition { margin-top: 48px; }
.evp-composition-head { max-width: 800px; margin-bottom: 22px; }
.evp-composition-head h2 { margin: 12px 0 10px; font-size: 25px; font-weight: 550; letter-spacing: -.015em; }
.evp-composition-head p { margin: 0; color: var(--ink-3); font-size: 13px; line-height: 1.85; }
.evp-composition-head .mono { color: var(--ink-2); font-size: 12px; }
.evp-structure-intro { max-width: 790px; margin: 38px 0 18px; }
.evp-structure-intro h3 { margin: 0 0 9px; font-size: 20px; font-weight: 550; letter-spacing: -.01em; }
.evp-structure-intro p { margin: 0; color: var(--ink-3); font-size: 12.5px; line-height: 1.85; }
.evp-structure-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 16px; }
@media (max-width:1100px) {
  .evp-structure-grid { grid-template-columns: 1fr; }
}
@media (max-width:900px) {
  .evp-role { grid-template-columns: 1fr; gap: 20px; }
  .evp-role-copy { max-width: 680px; }
  .evp-role-svg { max-width: 680px; }
}
@media (max-width:860px) {
  .evp-eras { grid-template-columns: repeat(2,1fr); }
  .evp-era { border-bottom: 1px solid var(--line); padding-bottom: 14px; }
  .evp-era:nth-child(2n) { border-right: none; }
}
</style>
