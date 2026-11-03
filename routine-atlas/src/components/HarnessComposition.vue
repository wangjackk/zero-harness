<script setup lang="ts">
type Implementation = {
  id: string
  name: string
  path: string
  status: string
  current?: boolean
  thesis: string
  chain: Array<{ name: string; sub: string }>
  inside: string[]
  loop: string
  action: string
  state: string
  note: string
}

const implementations: Implementation[] = [
  {
    id: 'flow', name: 'Flow', path: 'agents/flow', status: '代码实现 · 主清单未启用',
    thesis: '项目自己拥有执行循环。',
    chain: [
      { name: 'FlowHub', sub: 'HTTP / WS 前门' },
      { name: 'flow_manager', sub: '创建与管理' },
      { name: 'flow_agent', sub: 'Agent 实例' },
      { name: '工具 routine', sub: 'meta.tool' }
    ],
    inside: ['FlowLoop', 'EventLog · JSONL', 'Inbox · FIFO', 'ToolScheduler'],
    loop: 'turn / step；Hooks 提供循环扩展点',
    action: 'function call → create / start / wait；各调用立即并发',
    state: '每个 session 一份 append-only EventLog',
    note: 'FlowHub 只接入请求，flow_manager 创建 flow_agent。followup 排入后续轮次，steer 在步骤边界生效。'
  },
  {
    id: 'pi', name: 'Pi', path: 'agents/pi', status: 'TypeScript · 当前装配', current: true,
    thesis: '把成熟会话引擎接进 Routine。',
    chain: [
      { name: 'pi/agent/create', sub: '生命周期入口' },
      { name: 'pi/agent/manager', sub: '创建与管理' },
      { name: 'pi/agent', sub: '宿主包装' },
      { name: 'ipython', sub: 'custom tool' },
      { name: 'routine 能力', sub: '宿主调用' }
    ],
    inside: ['AgentSession · SDK', 'SessionManager · JSONL', 'ModelRuntime', 'routine-bridge'],
    loop: '会话主循环、压缩与重试由 Pi SDK 托管',
    action: '模型调用 ipython；AbortSignal 传播停止',
    state: 'SDK 管会话；共享 SQLite 只存注册元数据',
    note: 'manager 注册 Agent manifest 并创建 pi/agent。包装层翻译 SDK 事件，桥接项目模型配置和可停止的 routine 调用。'
  },
  {
    id: 'xml', name: 'XML', path: 'xml/agent', status: 'Python · 当前装配', current: true,
    thesis: '让行动随着模型输出流式发生。',
    chain: [
      { name: 'xml/agent/create', sub: '生命周期入口' },
      { name: 'xml/agent/manager', sub: '创建与管理' },
      { name: 'xml/agent', sub: 'epoch / react' },
      { name: 'act', sub: '每轮一个子实例' },
      { name: '工具 routine', sub: 'body Shell' }
    ],
    inside: ['Reflex', 'Memory · SQLite', 'LLMClient', 'body Shell'],
    loop: 'epoch 内反复 react；有效 feedback 驱动下一轮',
    action: 'XML 流 → Act 解析 → body Shell 派发',
    state: '消息、feedback、原始结果与摘要存入 SQLite',
    note: 'Act 是每轮创建的子 routine，不是普通解析函数。Reflex 决定在途输入应当丢弃、延后、放行还是打断。'
  },
  {
    id: 'moss', name: 'Moss', path: 'mossv2', status: '独立进程 · moss.yaml', current: true,
    thesis: '由注意力仲裁驱动连续拍链。',
    chain: [
      { name: 'asr / ui_adapter', sub: '感知与界面' },
      { name: 'mindflow', sub: '注意力仲裁' },
      { name: 'ghost', sub: '执行一拍' },
      { name: 'shell / channel', sub: '路由与动作通道' },
      { name: '动作 routine', sub: 'lane FIFO' }
    ],
    inside: ['Brain · 内部对象', 'Moments · 关键帧', 'observe / owed', 'CTML'],
    loop: 'attention / beat；不是 turn / step 循环',
    action: 'CTML → Shell route → Channel verb',
    state: 'Moments 组织感知、思考与行动关键帧',
    note: 'moss 固定装配身体，不使用 manager 模板。普通结果只入史；next、owed echo 或迟到 beat 才驱动续拍。'
  }
]
</script>

<template>
  <div class="hc">
    <article v-for="item in implementations" :key="item.id" class="implementation" :class="item.id">
      <header class="impl-head">
        <div class="impl-title">
          <span class="impl-id mono">{{ item.id.toUpperCase() }}</span>
          <h3>{{ item.name }}</h3>
          <code>{{ item.path }}</code>
        </div>
        <span class="impl-status" :class="{ current: item.current }">{{ item.status }}</span>
        <strong>{{ item.thesis }}</strong>
      </header>

      <div class="chain-scroll" tabindex="0" :aria-label="`${item.name} 真实装配关系，窄屏可横向滚动`">
        <div class="chain">
          <template v-for="(node, i) in item.chain" :key="node.name">
            <div class="chain-node">
              <b>{{ node.name }}</b><span>{{ node.sub }}</span>
            </div>
            <i v-if="i < item.chain.length - 1" aria-hidden="true">→</i>
          </template>
        </div>
      </div>

      <div class="inside" aria-label="内部实现组件">
        <span class="inside-label mono">内部实现</span>
        <code v-for="part in item.inside" :key="part">{{ part }}</code>
      </div>

      <dl class="facts">
        <div><dt>循环</dt><dd>{{ item.loop }}</dd></div>
        <div><dt>行动</dt><dd>{{ item.action }}</dd></div>
        <div><dt>状态</dt><dd>{{ item.state }}</dd></div>
      </dl>
      <p class="impl-note">{{ item.note }}</p>
    </article>
    <p class="hc-foot">链上节点是 routine；EventLog、AgentSession、Memory、Brain、Moments 等内部对象不因此变成新的基础单元。</p>
  </div>
</template>

<style scoped>
.hc { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.implementation { min-width: 0; overflow: hidden; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
.implementation.flow { border-top-color: color-mix(in oklab, var(--accent) 65%, var(--line)); }
.implementation.pi { border-top-color: color-mix(in oklab, #a98bfa 65%, var(--line)); }
.implementation.xml { border-top-color: color-mix(in oklab, #62b5e5 65%, var(--line)); }
.implementation.moss { border-top-color: color-mix(in oklab, #e4b66d 65%, var(--line)); }
.impl-head { display: grid; grid-template-columns: 1fr auto; gap: 10px 16px; padding: 22px 22px 19px; }
.impl-title { display: flex; align-items: baseline; flex-wrap: wrap; gap: 7px 11px; min-width: 0; }
.impl-id { color: var(--ink-4); font-size: 8px; letter-spacing: .12em; }
.impl-title h3 { color: var(--ink); font-size: 23px; font-weight: 540; letter-spacing: -.025em; }
.impl-title code { color: var(--ink-4); font-family: var(--font-mono); font-size: 9px; }
.impl-status { align-self: center; padding: 4px 7px; border: 1px solid var(--line-2); border-radius: 5px; color: var(--ink-4); font-size: 8.5px; white-space: nowrap; }
.impl-status.current { color: var(--ink-3); border-color: color-mix(in oklab, var(--accent) 30%, var(--line)); }
.impl-head > strong { grid-column: 1 / -1; color: var(--ink-2); font-size: 13px; font-weight: 480; }
.chain-scroll { overflow-x: auto; padding: 18px 16px; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); background: color-mix(in oklab, var(--bg-deep) 45%, transparent); scrollbar-width: thin; }
.chain { display: flex; align-items: center; gap: 5px; min-width: max-content; }
.chain-node { display: grid; gap: 5px; min-width: 80px; padding: 9px 8px; border: 1px solid color-mix(in oklab, var(--accent) 26%, var(--line)); border-radius: 7px; background: var(--surface-solid); }
.chain-node b { color: var(--ink-2); font-family: var(--font-mono); font-size: 8.5px; font-weight: 500; white-space: nowrap; }
.chain-node span { color: var(--ink-4); font-size: 8px; white-space: nowrap; }
.chain > i { color: var(--accent); font-size: 10px; font-style: normal; }
.inside { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; padding: 16px 22px 0; }
.inside-label { margin-right: 3px; color: var(--ink-4); font-size: 8px; letter-spacing: .08em; }
.inside code { padding: 4px 6px; border: 1px solid var(--line); border-radius: 4px; color: var(--ink-3); font-family: var(--font-mono); font-size: 8.5px; }
.facts { margin: 0; padding: 17px 22px 0; }
.facts > div { display: grid; grid-template-columns: 36px 1fr; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--line); }
.facts > div:last-child { border-bottom: 0; }
.facts dt { color: var(--ink-4); font-size: 10px; }
.facts dd { margin: 0; color: var(--ink-2); font-size: 11px; line-height: 1.7; }
.impl-note { min-height: 66px; margin: 12px 22px 20px; color: var(--ink-3); font-size: 11px; line-height: 1.8; }
.hc-foot { grid-column: 1 / -1; margin: 4px 0 0; color: var(--ink-4); font-size: 11px; line-height: 1.8; }
@media (max-width: 1080px) { .hc { grid-template-columns: 1fr; } .impl-note { min-height: 0; } }
@media (max-width: 540px) { .impl-head { grid-template-columns: 1fr; padding: 19px 17px 16px; } .impl-status { justify-self: start; } .chain-scroll { padding: 15px 16px; } .inside, .facts { padding-left: 17px; padding-right: 17px; } .impl-note { margin-left: 17px; margin-right: 17px; } }
</style>
