<script setup lang="ts">
import SocietyNetwork from '../components/SocietyNetwork.vue'
import { pageNo } from '../router'

const relations = [
  { no: '01', title: '平级协作', en: 'communication', text: '已经运行的人、Agent、工具与服务可以直接交换消息。它们不必属于同一棵实例树，也不因通信产生父子关系。' },
  { no: '02', title: '异构父子', en: 'invocation', text: '一个 routine 调用另一个 routine 时，双方不必同类型。父是 Flow Agent，子可以是 Pi Agent、人工规程或普通工具；父子关系来自调用。' },
  { no: '03', title: '开放接入', en: 'adaptation', text: '外部 Agent 提供接口时，可以由适配 routine 翻译协议、任务和结果。外部系统保持独立，接入不假装消灭边界。' }
]

const implementations = [
  { title: 'Agent Harness', text: '把上下文、模型请求、执行循环和结果回写组织起来，再通过 routine 调用文件、命令、检索等能力。', to: '/harness' },
  { title: '异构 Agent', text: 'Flow、Pi、XML、Moss 可以保留各自的内部机制，同时以 routine 的形式被发现、通信和调用。', to: '/harness' },
  { title: '机器人', text: '运动、跟踪、导航、地图与状态查询等 routine 通过 ROS 和设备总线连接真实机器人。', to: '/robot' },
  { title: '开放系统', text: '模型、工具、服务与外部 Agent 都能按输入输出接入；接口 routine 负责连接协议、任务与结果。', to: '/registry' }
]
</script>

<template>
  <div class="so">
    <section class="page so-head">
      <div class="kicker"><span>协作 · Routine Society</span><span class="dim">{{ pageNo('/society') }}</span></div>
      <h1>不是一群同类 Agent，<br /><em>而是一个异构 Routine 社会。</em></h1>
      <p class="lede">
        人、不同实现的 Agent、工具、服务与规程都能参与。统一的不是智能、类型或内部框架，
        而是被发现、通信、调用与管理的方式。多 Agent 只是这个网络中的一种组合。
      </p>
    </section>

    <section class="page so-network" aria-label="异构 Routine 网络">
      <SocietyNetwork />
    </section>

    <section class="page so-relations">
      <div class="sec-head" v-reveal>
        <span class="eyebrow">关系 · 不把协作混成调用</span>
        <h2>参与者可以异构，<br />关系仍然清楚。</h2>
        <p class="sec-lede">通信连接已有实例，调用创建子实例，接口适配跨越系统边界。三者可以组合，但语义不能互相替代。</p>
      </div>
      <div class="relation-grid">
        <article v-for="(r, i) in relations" :key="r.no" v-reveal="i * 70">
          <div class="relation-top"><span class="mono">{{ r.no }}</span><span class="mono">{{ r.en }}</span></div>
          <h3>{{ r.title }}</h3>
          <p>{{ r.text }}</p>
        </article>
      </div>
      <p class="so-caveat" v-reveal>
        Routine Core 提供统一的组合能力；具体 Harness 可以按任务需要，向模型开放创建、通信与子 Agent 入口。
      </p>
    </section>

    <section class="page so-boundary">
      <div class="boundary-copy" v-reveal>
        <span class="eyebrow">边界 · 外部不等于内部</span>
        <h2>接入外部 Agent，<br />不是把它变成内部实例。</h2>
        <p>
          内部调用创建的是适配 routine 的子实例。父停止可以回收这个内部子树；
          外部任务是否同步取消，取决于外部接口是否提供取消语义。超时、错误与结果关联也需要适配器明确处理。
        </p>
      </div>
      <div class="boundary-flow" v-reveal="80" aria-label="外部 Agent 接入路径">
        <span>内部 Agent</span><i>调用</i><span>接口 routine</span><i>协议</i><span class="external">外部 Agent</span>
      </div>
    </section>

    <section class="page so-implementations">
      <div class="sec-head" v-reveal>
        <span class="eyebrow">优势 · 从通用底座到真实实现</span>
        <h2>同一套 Routine，<br />生长出不同的系统。</h2>
        <p class="sec-lede">统一的注册、通信、调用与生命周期，让不同内部实现可以自由组合；Harness、Agent 与机器人都是这套底座的具体形态。</p>
      </div>
      <div class="implementation-grid">
        <RouterLink v-for="(item, i) in implementations" :key="item.title" :to="item.to" class="implementation-card" v-reveal="i * 50">
          <span class="mono">0{{ i + 1 }}</span><h3>{{ item.title }}</h3><p>{{ item.text }}</p><b>查看实现 →</b>
        </RouterLink>
      </div>
    </section>

    <section class="page so-next" v-reveal>
      <div><span class="eyebrow">回到个体</span><h2>网络描述整个系统，<br />Harness 描述一个 Agent。</h2></div>
      <RouterLink to="/harness" class="btn btn-solid">查看 Agent 内部结构 <span class="arw">→</span></RouterLink>
    </section>
  </div>
</template>

<style scoped>
.so { padding-bottom: 40px; }
.so-head { padding-top: clamp(64px, 9vw, 110px); padding-bottom: clamp(44px, 6vw, 72px); }
.kicker { display: flex; justify-content: space-between; color: var(--ink-4); font-family: var(--font-mono); font-size: 10px; letter-spacing: .1em; }
.so-head h1 { max-width: 1060px; margin-top: 22px; font-size: clamp(32px, 4.7vw, 62px); line-height: 1.2; letter-spacing: -.035em; font-weight: 520; }
.so-head h1 em { color: var(--accent); font-style: normal; }
.so-head .lede { max-width: 780px; margin-top: 26px; }
.so-network { padding-bottom: clamp(64px, 8vw, 100px); }
.sec-head { max-width: 790px; margin-bottom: 34px; }
.sec-head h2 { margin: 14px 0 18px; }
.sec-lede { color: var(--ink-3); font-size: 14px; line-height: 1.85; }
.relation-grid { display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid var(--line); border-radius: var(--r-lg); overflow: hidden; }
.relation-grid article { padding: 26px; border-right: 1px solid var(--line); background: var(--surface); }
.relation-grid article:last-child { border-right: 0; }
.relation-top { display: flex; justify-content: space-between; color: var(--ink-4); font-size: 9px; }
.relation-grid h3 { margin-top: 34px; color: var(--ink); font-size: 19px; font-weight: 520; }
.relation-grid p { margin-top: 13px; color: var(--ink-3); font-size: 12.5px; line-height: 1.85; }
.so-caveat { margin-top: 20px; padding-left: 15px; border-left: 2px solid var(--line-2); color: var(--ink-4); font-size: 11.5px; line-height: 1.8; }
.so-boundary { display: grid; grid-template-columns: 1fr 1.2fr; gap: 50px; align-items: center; margin-top: clamp(64px, 8vw, 100px); padding-top: 38px; padding-bottom: 38px; border-top: 1px solid var(--line-2); border-bottom: 1px solid var(--line-2); }
.boundary-copy h2 { margin-top: 15px; font-size: clamp(23px, 2.7vw, 34px); line-height: 1.4; }
.boundary-copy p { margin-top: 18px; color: var(--ink-3); font-size: 13px; line-height: 1.9; }
.boundary-flow { display: flex; align-items: center; justify-content: center; gap: 12px; flex-wrap: wrap; }
.boundary-flow span { padding: 14px 17px; border: 1px solid color-mix(in oklab, var(--accent) 40%, var(--line)); border-radius: 10px; background: var(--surface); color: var(--ink-2); font-size: 12px; }
.boundary-flow span.external { border-style: dashed; border-color: var(--line-2); color: var(--ink-4); }
.boundary-flow i { color: var(--ink-4); font-family: var(--font-mono); font-size: 9px; font-style: normal; }
.boundary-flow i::after { content: ' →'; color: var(--accent); }
.so-implementations { padding-top: clamp(64px, 8vw, 100px); padding-bottom: clamp(64px, 8vw, 100px); }
.implementation-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.implementation-card { display: flex; flex-direction: column; padding: 23px; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); color: inherit; text-decoration: none; transition: border-color .25s ease, transform .25s ease; }
.implementation-card:hover { border-color: var(--accent); transform: translateY(-2px); }
.implementation-card > span { color: var(--accent); font-size: 9px; }
.implementation-card h3 { margin-top: 24px; color: var(--ink); font-size: 16px; font-weight: 520; }
.implementation-card p { margin-top: 11px; color: var(--ink-3); font-size: 12px; line-height: 1.85; }
.implementation-card b { margin-top: 22px; padding-top: 14px; border-top: 1px solid var(--line); color: var(--accent); font-size: 10px; font-weight: 500; }
.so-next { display: flex; align-items: end; justify-content: space-between; gap: 30px; padding-top: 36px; padding-bottom: 36px; border-top: 1px solid var(--line-2); border-bottom: 1px solid var(--line-2); }
.so-next h2 { margin-top: 14px; font-size: clamp(22px, 2.8vw, 34px); line-height: 1.4; }
.so-next .btn { flex-shrink: 0; }
@media (max-width: 900px) {
  .relation-grid { grid-template-columns: 1fr; }
  .relation-grid article { border-right: 0; border-bottom: 1px solid var(--line); }
  .relation-grid article:last-child { border-bottom: 0; }
  .so-boundary { grid-template-columns: 1fr; gap: 28px; }
  .implementation-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 560px) {
  .so-head h1 { font-size: 32px; }
  .implementation-grid { grid-template-columns: 1fr; }
  .so-next { align-items: start; flex-direction: column; }
  .boundary-flow { justify-content: flex-start; }
}
</style>
