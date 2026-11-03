<script setup lang="ts">
import AgentRoutineOrbit from '../components/AgentRoutineOrbit.vue'
import { pageNo } from '../router'

const groups = [
  {
    title: '设备接入', en: 'DEVICE BRIDGES',
    description: '把机器人设备的消息引进来，也把软件请求发出去。',
    routines: [
      { name: 'ros_bridge', text: '桥接 ROS 话题的发布与订阅。' },
      { name: 'bus_listener', text: '接入语音、视觉与车头相机等设备事件。' }
    ]
  },
  {
    title: '运动', en: 'MOTION',
    description: '把移动意图变成底盘可以执行的动作。',
    routines: [
      { name: 'car/move', text: '前进、后退与左右平移。' },
      { name: 'car/turn', text: '按角度原地旋转。' },
      { name: 'car/steer', text: '以前进速度和转向角速度弧线行驶。' },
      { name: 'car/shake', text: '执行左右摆动。' },
      { name: 'car/stop', text: '打断当前运动并发送零速。' }
    ]
  },
  {
    title: '跟踪与联动', en: 'TRACKING',
    description: '把视觉、语音事件与运动能力组合起来。',
    routines: [
      { name: 'car/track', text: '根据视觉目标进行跟踪。' },
      { name: 'car/gaze', text: '在 Agent 交互期间保持注视。' },
      { name: 'car/wake_track', text: '唤醒后转向声源，再衔接视觉跟踪。' }
    ]
  },
  {
    title: '导航与地图', en: 'NAVIGATION',
    description: '使用车上的地图、定位和导航服务。',
    routines: [
      { name: 'car/navigate', text: '导航到已标定的命名区域。' },
      { name: 'car/navigate/manage', text: '启动、停止或重启导航栈。' },
      { name: 'car/map', text: '获取地图、区域与当前位置。' },
      { name: 'car/where', text: '查询机器人当前所在区域。' }
    ]
  },
  {
    title: '状态与相机', en: 'OBSERVATION',
    description: '把设备状态与现场画面作为调用结果带回。',
    routines: [
      { name: 'car/status', text: '查询电池、电压与里程计状态。' },
      { name: 'car/snap', text: '使用车头相机拍照并返回图片。' }
    ]
  }
]

const agentDesigns = [
  {
    name: 'XML Agent', en: 'STREAMING ACTIONS',
    summary: '让行动随着模型输出流式发生。',
    features: [
      { title: '流式', text: '模型输出的 XML 字流持续送入 Act；解析出行动后便调用 routine，工具结果再流式回到 Agent。' },
      { title: '全双工交互', text: 'Reflex 处理生成与行动期间抵达的新输入，决定延后、放行或打断，不把交互锁成固定问答轮次。' },
      { title: '具身编排', text: 'Act 通过 body Shell 调用 car/*；manager 还可以接收语音事件并联动 wake_track、gaze 等身体能力。' }
    ]
  },
  {
    name: 'Moss', en: 'CONTINUOUS ORCHESTRATION',
    summary: '让感知、思考与行动形成连续过程。',
    features: [
      { title: '流式', text: 'Ghost 将模型字流持续交给 Shell；标签到达即创建 routine，不必等整段计划写完才开始。' },
      { title: '连续交互', text: 'Mindflow 持续接收 ASR 与其他输入，仲裁何时继续、打断或续拍；当前自有语音面默认说话期丢麦，听说为半双工。' },
      { title: '编排', text: 'CTML 将动作写入不同 lane；同轨先后、分轨并行，all / any / loop 等容器组合更复杂的行动过程。' }
    ]
  }
]
</script>

<template>
  <div class="ro">
    <header class="page ro-head">
      <div class="ro-kicker"><span>机器人 · Agent 与 Routine 身体</span><span class="dim">{{ pageNo('/robot') }}</span></div>
      <h1>机器人 Agent，<br /><em>与它的 routine 身体。</em></h1>
      <p class="lede">
        XML 与 Moss 提供不同的流式交互和行动编排；运动、跟踪、导航与状态查询由 routine 组成。
        它们既能连接真实设备，也能与 Pi、Flow 等异构 Agent 在同一个 Routine 社会中协作。
      </p>
    </header>

    <section class="page ro-agent" aria-label="机器人 Agent 的流式交互与编排">
      <div class="sec-head" v-reveal>
        <span class="eyebrow">Agent 本身 · 两种具身实现</span>
        <h2>流式交互，<br />持续编排行动。</h2>
        <p class="sec-lede">这里讨论 Agent 如何接收输入、产生输出并组织行动；身体能力仍由下面的普通 routine 提供。</p>
      </div>
      <div class="agent-designs">
        <article v-for="(agent, i) in agentDesigns" :key="agent.name" v-reveal="i * 70">
          <header><span class="mono">{{ agent.en }}</span><h3>{{ agent.name }}</h3><p>{{ agent.summary }}</p></header>
          <div v-for="feature in agent.features" :key="feature.title" class="agent-feature">
            <strong>{{ feature.title }}</strong><p>{{ feature.text }}</p>
          </div>
        </article>
      </div>
      <div class="agent-society" v-reveal>
        <div><span class="mono">同一个 ROUTINE 社会</span><h3>内部机制不同，协作方式相同。</h3></div>
        <p><strong>Pi · Flow · XML · Moss</strong> 都保留各自的 Harness，同时以 routine 的形式被发现、通信和调用。平级通信用于协作，调用则形成异构父子实例。</p>
      </div>
    </section>

    <section class="page ro-shared" aria-label="四种 Agent 共享调用底层 routine">
      <div class="sec-head shared-head" v-reveal>
        <div><span class="eyebrow">共享身体 · 能力流转</span><h2>同一批能力，<br />在不同 Agent 间流转。</h2></div>
        <p class="sec-lede">运动、跟踪、导航、状态与设备桥接不专属于某一种 Agent。Pi、Flow、XML、Moss 可以按各自的执行方式，从同一批底层 routine 中选择和组合能力。</p>
      </div>
      <AgentRoutineOrbit v-reveal />
      <p class="shared-note" v-reveal>同一组行星贯穿四个星系；改变的是编排中心，不是底层能力。任意 Agent 都可以调用同一项已开放能力。</p>
    </section>

    <section class="page ro-parts" aria-label="机器人由哪些 routine 构成">
      <div class="sec-head" v-reveal>
        <span class="eyebrow">当前实现 · 16 个 routine</span>
        <h2>由哪些 routine 构成。</h2>
        <p class="sec-lede">两个设备接入 routine，加上十四个 <code>car/*</code> 能力 routine。</p>
      </div>
      <div class="routine-groups">
        <article v-for="(group, i) in groups" :key="group.title" v-reveal="i * 45">
          <header><span class="mono">{{ group.en }}</span><h3>{{ group.title }}</h3><p>{{ group.description }}</p></header>
          <ul>
            <li v-for="routine in group.routines" :key="routine.name">
              <code>{{ routine.name }}</code><span>{{ routine.text }}</span>
            </li>
          </ul>
        </article>
      </div>
    </section>

    <section class="page ro-bridge" aria-label="软件与硬件如何结合">
      <div class="sec-head" v-reveal>
        <span class="eyebrow">软硬结合 · 调用与消息</span>
        <h2>软件发出调用，<br />硬件执行并反馈。</h2>
      </div>
      <div class="bridge-steps">
        <article v-reveal>
          <span class="mono">01 · 软件调用</span>
          <h3>Agent 调用能力 routine</h3>
          <p>例如调用 <code>car/move</code> 给定方向和速度，或调用 <code>car/navigate</code> 给定目标区域。调用方不需要直接操作电机或设备 SDK。</p>
        </article>
        <article v-reveal="60">
          <span class="mono">02 · 协议转换</span>
          <h3>routine 发送设备消息</h3>
          <p>运动实现把参数转换为速度消息，经 rosbridge WebSocket 发往 <code>/controller/cmd_vel</code>；导航目标发往 <code>/nav_goal</code>。拍照请求则经设备总线发往车头相机。</p>
        </article>
        <article v-reveal="120">
          <span class="mono">03 · 数据返回</span>
          <h3>设备数据回到 routine</h3>
          <p>电池、里程计和 AMCL 位姿经 <code>ros_bridge</code> 返回；语音与视觉事件经 <code>bus_listener</code> 返回。状态、地图、跟踪等 routine 读取这些数据并产生结果。</p>
        </article>
      </div>
      <p class="bridge-summary" v-reveal>
        <strong>结合点不是一种新的机器人类型，而是 routine 的输入与输出。</strong>
        一侧是 Agent 和其他软件单元，另一侧是底盘、导航栈、相机与传感器。
      </p>
      <div class="module-application" v-reveal>
        <p><strong>机器人也使用 Routine Core 的模块系统。</strong>运动与跟踪等 routine 通过 <code>wheels</code> 声明底盘占用，再按通用冲突与优先级规则协调执行。</p>
        <RouterLink to="/modules" class="text-link">查看模块系统 <span>→</span></RouterLink>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ro { padding-bottom: 56px; }
.ro-head { padding-top: clamp(64px, 9vw, 110px); padding-bottom: clamp(44px, 6vw, 72px); }
.ro-kicker { display: flex; justify-content: space-between; color: var(--ink-4); font-family: var(--font-mono); font-size: 10px; letter-spacing: .1em; }
.ro-head h1 { max-width: 980px; margin-top: 22px; font-size: clamp(32px, 4.5vw, 58px); line-height: 1.22; letter-spacing: -.035em; font-weight: 520; }
.ro-head h1 em { color: var(--accent); font-style: normal; }
.ro-head .lede { max-width: 800px; margin-top: 26px; }
.sec-head { max-width: 800px; margin-bottom: 32px; }
.sec-head h2 { margin: 14px 0 18px; }
.sec-lede { color: var(--ink-3); font-size: 14px; line-height: 1.85; }
.sec-lede code, .routine-groups code, .bridge-steps code { color: var(--ink-2); font-family: var(--font-mono); font-size: .92em; }
.ro-agent { padding-bottom: clamp(70px, 9vw, 112px); }
.agent-designs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.agent-designs > article { padding: 28px; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
.agent-designs > article > header > span { color: #a98bfa; font-size: 9px; letter-spacing: .1em; }
.agent-designs h3 { margin-top: 12px; color: var(--ink); font-size: 25px; font-weight: 520; }
.agent-designs > article > header > p { margin-top: 9px; color: var(--ink-2); font-size: 13.5px; line-height: 1.75; }
.agent-feature { display: grid; grid-template-columns: 104px 1fr; gap: 18px; padding: 18px 0; border-top: 1px solid var(--line); }
.agent-feature:first-of-type { margin-top: 22px; }
.agent-feature strong { color: var(--ink-2); font-size: 13px; font-weight: 550; }
.agent-feature p { color: var(--ink-3); font-size: 12.5px; line-height: 1.85; }
.agent-society { display: grid; grid-template-columns: .9fr 1.1fr; gap: 38px; align-items: center; margin-top: 18px; padding: 26px 28px; border: 1px solid color-mix(in oklab, var(--accent) 28%, var(--line)); border-radius: var(--r-lg); background: color-mix(in oklab, var(--accent) 4%, var(--surface)); }
.agent-society span { color: var(--accent); font-size: 9px; letter-spacing: .09em; }
.agent-society h3 { margin-top: 11px; color: var(--ink); font-size: 19px; font-weight: 520; }
.agent-society p { color: var(--ink-3); font-size: 13px; line-height: 1.9; }
.agent-society strong { color: var(--ink-2); font-family: var(--font-mono); font-size: 12px; font-weight: 500; }
.ro-shared { padding-bottom: clamp(70px, 9vw, 112px); }
.shared-head { display: grid; grid-template-columns: 1fr 1fr; gap: 60px; align-items: end; max-width: none; }
.shared-head h2 { margin-bottom: 0; }
.shared-head .sec-lede { padding-bottom: 3px; }
.shared-note { max-width: 750px; margin: 18px auto 0; color: var(--ink-3); font-size: 12.5px; line-height: 1.85; text-align: center; }
.ro-parts { padding-bottom: clamp(70px, 9vw, 112px); }
.routine-groups { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; align-items: start; }
.routine-groups article { min-width: 0; padding: 27px 28px 20px; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
.routine-groups article > header > span { color: #a98bfa; font-size: 9px; letter-spacing: .1em; }
.routine-groups h3 { margin-top: 12px; color: var(--ink); font-size: 22px; font-weight: 520; }
.routine-groups header p { margin-top: 10px; color: var(--ink-3); font-size: 13px; line-height: 1.75; }
.routine-groups ul { margin-top: 22px; list-style: none; }
.routine-groups li { display: grid; grid-template-columns: minmax(128px, .8fr) 1.2fr; gap: 16px; align-items: baseline; padding: 14px 0; border-top: 1px solid var(--line); }
.routine-groups li code { color: var(--ink); font-size: 12px; overflow-wrap: anywhere; }
.routine-groups li > span { color: var(--ink-3); font-size: 12.5px; line-height: 1.7; }
.ro-bridge { padding-top: clamp(54px, 7vw, 82px); border-top: 1px solid var(--line-2); }
.bridge-steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.bridge-steps article { padding: 25px 26px; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
.bridge-steps article > span { color: var(--accent); font-size: 9px; letter-spacing: .08em; }
.bridge-steps h3 { margin-top: 20px; color: var(--ink); font-size: 18px; font-weight: 520; line-height: 1.5; }
.bridge-steps p { margin-top: 14px; color: var(--ink-3); font-size: 13px; line-height: 1.9; }
.bridge-summary { margin-top: 22px; padding: 20px 24px; border-left: 2px solid var(--accent); background: color-mix(in oklab, var(--accent) 4%, transparent); color: var(--ink-3); font-size: 13px; line-height: 1.9; }
.bridge-summary strong { color: var(--ink-2); font-weight: 550; }
.module-application { display: flex; align-items: center; justify-content: space-between; gap: 28px; margin-top: 14px; padding: 20px 24px; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
.module-application p { color: var(--ink-3); font-size: 12.5px; line-height: 1.85; }
.module-application strong { color: var(--ink-2); font-weight: 550; }
.module-application code { color: var(--accent); font-family: var(--font-mono); font-size: .92em; }
.module-application .text-link { flex-shrink: 0; }
@media (max-width: 850px) {
  .agent-designs, .agent-society, .shared-head, .routine-groups, .bridge-steps { grid-template-columns: 1fr; }
  .agent-society, .shared-head { gap: 18px; }
}
@media (max-width: 540px) {
  .ro-head h1 { font-size: 32px; }
  .agent-designs > article, .agent-society, .routine-groups article, .bridge-steps article { padding: 22px; }
  .agent-feature { grid-template-columns: 1fr; gap: 7px; }
  .routine-groups li { grid-template-columns: 1fr; gap: 6px; }
  .routine-groups li code { font-size: 12.5px; }
  .bridge-summary { padding: 18px; }
  .module-application { align-items: flex-start; flex-direction: column; gap: 14px; padding: 20px; }
}
</style>
