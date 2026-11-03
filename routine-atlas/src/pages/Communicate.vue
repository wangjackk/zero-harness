<script setup lang="ts">
import Communication from '../components/Communication.vue'
import DeviceInterconnect from '../components/DeviceInterconnect.vue'
import { pageNo } from '../router'
</script>

<template>
  <div class="cm">
    <div class="page">
      <header class="cm-head">
        <div class="cm-kicker">
          <span class="eyebrow">通信 · 多个 actor 之间</span>
          <span class="dim mono">{{ pageNo('/communication') }}</span>
        </div>
        <h1>调用组织执行，<br /><em>通信连接现场。</em></h1>
        <p class="cm-lede">
          一个 routine 调用另一个 routine，会创建命令实例并形成父子树。
          但正在运行的 actor 也可以直接交换消息：发布一个事件，订阅一个主题，或向已有实例发送请求。
        </p>
      </header>

      <div class="cm-distinction">
        <article>
          <span class="cm-no mono">CALL</span>
          <h2>调用</h2>
          <p>一个单元在执行中创建另一个命令实例；调用关系同时规定父子生命周期。</p>
        </article>
        <article>
          <span class="cm-no mono">MESSAGE</span>
          <h2>通信</h2>
          <p>已经运行的单元直接交换信息；消息本身不创建节点，也不改变实例树。</p>
        </article>
      </div>

      <Communication />

      <section class="cm-devices" aria-label="多类型设备通过 RoutineHub 互联">
        <div class="device-copy" v-reveal>
          <span class="eyebrow">跨设备 · 多种设备，同一个 Routine 系统</span>
          <h2>设备成为节点，<br />能力自动集成。</h2>
          <p>手机、电脑、机器人或其他设备，只要能运行 RoutineHub，并能通过 gRPC 连接到 kernel，就能成为系统节点。本机 routine 随 hub 注册自动进入同一注册表，无需为每种设备另做一套调用体系；节点不仅能被调用，也能发布与订阅消息，并主动调用其他节点的 routine。</p>
        </div>
        <DeviceInterconnect v-reveal />
        <div class="device-facts" v-reveal>
          <p><strong>设备不是单个 routine</strong>设备运行 hub，由 hub 提供本机的一组 routine；每个 hub 都同时可以作为服务方、消息方与调用方。</p>
          <p><strong>手机拨入，不持有 kernel</strong><code>phone-hub</code> 通过 <code>Kernel.remote(address)</code> 连接其他进程或设备上的 kernel；手机作为系统 hub 节点接入。</p>
          <p><strong>感知可以主动上行</strong>真实实现 <code>phone/sensors/watch</code> 持续 <code>publish</code> 到 <code>phone/sensors</code>，任意 hub 上的订阅者都可以接收。</p>
          <p><strong>调用可以跨 hub 发起</strong>routine 通过 <code>call(name)</code> 按注册名调用其他设备的能力，例如桌面 hub 主动调用机器人的 <code>car/move</code>，再接收返回结果。</p>
        </div>
      </section>

      <div class="cm-notes">
        <p><strong>listener ↔ agent</strong>两者都是 routine；通信可以双向流动，但不创建节点。</p>
        <p><strong>agent → routine</strong>调用创建临时子节点，并建立父子生命周期。</p>
        <p><strong>只有两种关系</strong>通信连接已有节点，调用生长临时节点；不再拆分其他关系。</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cm { padding: 96px 0 40px; }
.cm .page { max-width: 1320px; }
.cm-head { max-width: 900px; }
.cm-kicker { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.cm-kicker .dim { font-size: 10px; }
.cm-head h1 { margin: 16px 0 18px; font-size: clamp(36px, 5.1vw, 66px); line-height: 1.12; letter-spacing: -.035em; font-weight: 570; }
.cm-head h1 em { color: var(--accent); font-style: normal; }
.cm-lede { max-width: 800px; margin: 0; color: var(--ink-3); font-size: 14px; line-height: 1.95; }
.cm-distinction { margin-top: 34px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.cm-distinction article { position: relative; padding: 22px 24px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }
.cm-no { position: absolute; top: 23px; right: 24px; color: var(--ink-4); font-size: 9px; letter-spacing: .08em; }
.cm-distinction h2 { margin: 0 0 9px; font-size: 18px; font-weight: 550; }
.cm-distinction p { margin: 0; max-width: 520px; color: var(--ink-3); font-size: 12px; line-height: 1.8; }
.cm-distinction code { color: var(--ink-2); font-family: var(--font-mono); font-size: .92em; }
.cm-devices { padding-top: clamp(72px, 9vw, 116px); }
.device-copy { max-width: 820px; margin-bottom: 30px; }
.device-copy h2 { margin: 14px 0 18px; }
.device-copy p { color: var(--ink-3); font-size: 14px; line-height: 1.85; }
.device-facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 20px; margin-top: 20px; }
.device-facts p { padding-top: 13px; border-top: 1px solid var(--line); color: var(--ink-4); font-size: 11px; line-height: 1.8; }
.device-facts strong { display: block; margin-bottom: 4px; color: var(--ink-2); font-weight: 500; }
.device-facts code { color: var(--ink-3); font-family: var(--font-mono); font-size: .92em; }
.cm-notes { margin-top: 20px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }
.cm-notes p { margin: 0; padding-top: 13px; border-top: 1px solid var(--line); color: var(--ink-4); font-size: 11px; line-height: 1.8; }
.cm-notes strong { display: block; margin-bottom: 4px; color: var(--ink-2); font-weight: 500; }
@media (max-width: 1100px) and (min-width: 761px) {
  .device-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .cm { padding-top: 72px; }
  .cm-distinction, .cm-notes, .device-facts { grid-template-columns: 1fr; }
  .cm-notes, .device-facts { gap: 12px; }
}
</style>
