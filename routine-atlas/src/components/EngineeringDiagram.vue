<script setup lang="ts">
import { computed } from 'vue'

type Side = 'left' | 'right' | 'top' | 'bottom'
interface DiagramGroup { x: number; y: number; w: number; h: number; label: string; tone?: string }
interface DiagramNode { id: string; x: number; y: number; w?: number; h?: number; title: string; subtitle?: string; tone?: string }
interface DiagramEdge { from: string; to: string; label?: string; fromSide?: Side; toSide?: Side; dashed?: boolean; both?: boolean; d?: string; labelX?: number; labelY?: number }
interface Diagram { title: string; height: number; caption: string; groups: DiagramGroup[]; nodes: DiagramNode[]; edges: DiagramEdge[] }

const props = defineProps<{ name: keyof typeof DIAGRAMS }>()
const diagram = computed(() => DIAGRAMS[props.name])
const nodes = computed(() => new Map(diagram.value.nodes.map(n => [n.id, n])))
const markerId = computed(() => `eng-${props.name}-arrow`)

function anchor(node: DiagramNode, side: Side) {
  const w = node.w ?? 150
  const h = node.h ?? 66
  if (side === 'left') return { x: node.x, y: node.y + h / 2 }
  if (side === 'right') return { x: node.x + w, y: node.y + h / 2 }
  if (side === 'top') return { x: node.x + w / 2, y: node.y }
  return { x: node.x + w / 2, y: node.y + h }
}
function edgeGeometry(edge: DiagramEdge) {
  const from = nodes.value.get(edge.from)
  const to = nodes.value.get(edge.to)
  if (!from || !to) return { d: '', x: 0, y: 0 }
  const fromSide = edge.fromSide ?? 'right'
  const toSide = edge.toSide ?? 'left'
  const a = anchor(from, fromSide)
  const b = anchor(to, toSide)
  let d = edge.d ?? ''
  if (!d) {
    if (fromSide === 'top' || fromSide === 'bottom') {
      const bend = Math.max(24, Math.abs(b.y - a.y) * .45)
      d = `M ${a.x} ${a.y} C ${a.x} ${a.y + (fromSide === 'bottom' ? bend : -bend)}, ${b.x} ${b.y + (toSide === 'bottom' ? bend : -bend)}, ${b.x} ${b.y}`
    } else {
      const bend = Math.max(28, Math.abs(b.x - a.x) * .45)
      d = `M ${a.x} ${a.y} C ${a.x + (fromSide === 'right' ? bend : -bend)} ${a.y}, ${b.x + (toSide === 'right' ? bend : -bend)} ${b.y}, ${b.x} ${b.y}`
    }
  }
  return { d, x: edge.labelX ?? (a.x + b.x) / 2, y: edge.labelY ?? (a.y + b.y) / 2 - 7 }
}
function lines(text?: string) { return text ? text.split('\n') : [] }

const DIAGRAMS: Record<string, Diagram> = {
  boot: {
    title: '从启动清单到运行时注册表', height: 500,
    caption: '实线表示启动路径，虚线表示热更新路径；Loader 与 Watcher 共用同一个 yaml_path()。',
    groups: [
      { x: 20, y: 48, w: 220, h: 390, label: '输入与监听', tone: 'external' },
      { x: 260, y: 48, w: 340, h: 390, label: 'zero-py · 导入与收敛', tone: 'control' },
      { x: 620, y: 48, w: 360, h: 390, label: 'RoutineHub → kernel', tone: 'runtime' },
    ],
    nodes: [
      { id: 'main', x: 45, y: 90, w: 170, title: 'app.main', subtitle: 'CLI / ZERO_ROUTINES_YAML', tone: 'external' },
      { id: 'yaml', x: 45, y: 185, w: 170, title: 'routines.yaml', subtitle: '条目 · kwargs · modules', tone: 'storage' },
      { id: 'source', x: 45, y: 280, w: 170, title: '包与源码', subtitle: '__init__.py / *.py', tone: 'storage' },
      { id: 'poll', x: 45, y: 360, w: 170, title: 'RoutinesWatcher', subtitle: '1s poll + 500ms debounce', tone: 'control' },
      { id: 'check', x: 285, y: 90, w: 290, title: 'check_yaml → RoutinesLoader', subtitle: '启动前预检；唯一静态注册的 passive', tone: 'control' },
      { id: 'manifest', x: 285, y: 185, w: 290, title: 'import → __all__ → Routines', subtitle: '显式导出；注入 PassiveConfig kwargs', tone: 'control' },
      { id: 'reload', x: 285, y: 280, w: 290, title: '子模块与祖先包 reload', subtitle: 'manifest diff · 删除墓碑 · 失败保留 mtime', tone: 'control' },
      { id: 'apply', x: 285, y: 360, w: 290, title: '注册变更', subtitle: 'register / reload / deregister', tone: 'control' },
      { id: 'catalog', x: 645, y: 105, w: 135, title: 'catalog', subtitle: '名称 → 类与配置', tone: 'runtime' },
      { id: 'hub', x: 815, y: 105, w: 135, title: 'kernel 路由', subtitle: '注册表唯一事实源', tone: 'runtime' },
      { id: 'instances', x: 645, y: 245, w: 305, title: '命令实例生命周期', subtitle: 'passive 自动拉起；reload 停旧实例并 auto-start 新实例', tone: 'runtime' },
      { id: 'retry', x: 645, y: 360, w: 305, title: '失败收敛', subtitle: '导入异常不吞变更；下轮 poll 重试，手动 reload 走同一链路', tone: 'boundary' },
    ],
    edges: [
      { from: 'main', to: 'check', label: '定一次清单路径' },
      { from: 'yaml', to: 'manifest', label: '解析条目' },
      { from: 'check', to: 'manifest', fromSide: 'bottom', toSide: 'top' },
      { from: 'manifest', to: 'catalog', label: '逐类注册' },
      { from: 'catalog', to: 'hub' },
      { from: 'source', to: 'poll', fromSide: 'bottom', toSide: 'top', dashed: true },
      { from: 'poll', to: 'reload', label: '变更集', dashed: true },
      { from: 'yaml', to: 'reload', dashed: true, d: 'M 215 218 C 250 218, 245 313, 285 313', labelX: 244, labelY: 208 },
      { from: 'reload', to: 'apply', fromSide: 'bottom', toSide: 'top', dashed: true },
      { from: 'apply', to: 'hub', label: 'upsert / remove', dashed: true },
      { from: 'hub', to: 'instances', fromSide: 'bottom', toSide: 'top', label: 'create / restart', labelX: 866, labelY: 214 },
      { from: 'reload', to: 'retry', dashed: true, d: 'M 575 313 C 615 313, 605 393, 645 393', labelX: 606, labelY: 305 },
    ],
  },
  voice: {
    title: '独立听觉服务与 zero-rs 流式合成', height: 690,
    caption: 'sherpa-onnx/services 与 zero-rs 是两条独立实现；本图不表示 Moss 的当前语音接线，Moss 自有 ASR / TTS 见第 05 章。',
    groups: [
      { x: 20, y: 48, w: 610, h: 590, label: 'sherpa-onnx/services · ZeroMQ PUB/SUB', tone: 'external' },
      { x: 650, y: 48, w: 330, h: 590, label: 'zero-harness · zero-rs 说话链', tone: 'runtime' },
    ],
    nodes: [
      { id: 'mic', x: 40, y: 90, w: 165, title: 'micserver-rs', subtitle: '麦克风 · 100ms PCM', tone: 'external' },
      { id: 'raw', x: 235, y: 90, w: 170, title: 'voice.audio.raw', subtitle: 'seq + int16 PCM', tone: 'data' },
      { id: 'aec', x: 435, y: 90, w: 170, title: 'aecserver-rs', subtitle: 'AEC3 + NS · 参考对齐', tone: 'control' },
      { id: 'kws', x: 40, y: 210, w: 165, title: 'kws-rs / kwsxf-rs', subtitle: '本地 / 板载双路唤醒', tone: 'control' },
      { id: 'vad', x: 235, y: 210, w: 170, title: 'vad-rs', subtitle: 'speech_start / stop', tone: 'control' },
      { id: 'asr', x: 435, y: 210, w: 170, title: 'asrserver-rs', subtitle: 'sid 会话 · partial / final', tone: 'control' },
      { id: 'broker', x: 235, y: 325, w: 170, h: 78, title: 'broker-rs', subtitle: 'XSUB :6666\nXPUB :6667', tone: 'data' },
      { id: 'orch', x: 40, y: 335, w: 165, h: 92, title: 'orch-rs', subtitle: 'Policy + Phase\n开轮 / 收轮 / 放弃', tone: 'control' },
      { id: 'playback', x: 435, y: 335, w: 170, title: 'playback-rs', subtitle: '混音 → 喇叭实播镜像', tone: 'external' },
      { id: 'events', x: 235, y: 465, w: 170, h: 82, title: 'voice.*.evt', subtitle: 'wake / asr / orch\nstate / result / aborted', tone: 'data' },
      { id: 'config', x: 435, y: 475, w: 170, title: 'voice.toml', subtitle: 'bus-config 共享部署配置', tone: 'storage' },
      { id: 'agent', x: 680, y: 90, w: 270, title: 'speak 调用方（如 XML Agent）', subtitle: '产生文本流；与左侧听觉服务无默认接线', tone: 'agent' },
      { id: 'speak', x: 680, y: 205, w: 270, title: 'speak 实例', subtitle: 'text / _eof / stop · audio_mouth 互斥', tone: 'runtime' },
      { id: 'actor', x: 680, y: 315, w: 270, title: 'TtsService actor', subtitle: 'token 隔离 · 实例 FIFO · 常驻 Seed WS', tone: 'runtime' },
      { id: 'audio', x: 680, y: 425, w: 270, title: 'AudioService', subtitle: 'PcmSink typed channel · 设备播放与 drain', tone: 'runtime' },
      { id: 'emotion', x: 680, y: 545, w: 270, title: 'agent/state', subtitle: '首个 Feed 建会话前读取 tts_instruction', tone: 'agent' },
    ],
    edges: [
      { from: 'mic', to: 'raw', label: '采集' },
      { from: 'raw', to: 'aec', label: 'capture' },
      { from: 'raw', to: 'kws', dashed: true, d: 'M 235 123 C 175 123, 185 243, 122 243', labelX: 170, labelY: 156 },
      { from: 'raw', to: 'vad', fromSide: 'bottom', toSide: 'top', dashed: true },
      { from: 'raw', to: 'asr', fromSide: 'bottom', toSide: 'top', dashed: true },
      { from: 'playback', to: 'aec', fromSide: 'top', toSide: 'bottom', label: 'voice.play.mix', labelX: 535, labelY: 184, both: true },
      { from: 'aec', to: 'orch', dashed: true, d: 'M 435 145 C 330 145, 265 360, 122 381', labelX: 290, labelY: 173 },
      { from: 'kws', to: 'orch', fromSide: 'bottom', toSide: 'top', label: 'voice.wake', labelX: 115, labelY: 306 },
      { from: 'vad', to: 'orch', fromSide: 'bottom', toSide: 'top', label: 'vad.evt / cmd', both: true, labelX: 292, labelY: 307 },
      { from: 'asr', to: 'orch', fromSide: 'bottom', toSide: 'top', label: 'asr.evt / cmd', both: true, labelX: 470, labelY: 307 },
      { from: 'orch', to: 'broker', label: 'state' },
      { from: 'asr', to: 'broker', dashed: true, d: 'M 520 276 C 520 364, 405 364, 405 364' },
      { from: 'playback', to: 'broker', dashed: true, d: 'M 520 401 C 520 440, 405 390, 405 390' },
      { from: 'broker', to: 'events', fromSide: 'bottom', toSide: 'top' },
      { from: 'agent', to: 'speak', fromSide: 'bottom', toSide: 'top', label: '文本流' },
      { from: 'speak', to: 'actor', fromSide: 'bottom', toSide: 'top', label: 'Feed / Finish / Cancel' },
      { from: 'actor', to: 'audio', fromSide: 'bottom', toSide: 'top', label: 'PCM + end / stop' },
      { from: 'emotion', to: 'actor', fromSide: 'top', toSide: 'bottom', dashed: true, label: '情绪采样', labelX: 865, labelY: 500 },
    ],
  },
  mossVoice: {
    title: 'Moss 自有 ASR / TTS 语音面（参考原版 Moss）', height: 570,
    caption: '当前 moss.yaml 装配 mossv2/asr 与 commands/say.py；听觉直连豆包 SAUC，说话使用 SeedTTS + LocalSpeaker，不经过 sherpa-onnx/services 或 zero-rs speak。',
    groups: [
      { x: 20, y: 48, w: 610, h: 470, label: 'mossv2 · 听觉、判停与注意力', tone: 'agent' },
      { x: 650, y: 48, w: 330, h: 470, label: 'mossv2 · 说话与本地播放', tone: 'runtime' },
    ],
    nodes: [
      { id: 'mic', x: 40, y: 90, w: 165, title: 'sounddevice 麦克风', subtitle: '40ms 采集 · 200ms 上传', tone: 'external' },
      { id: 'ear', x: 235, y: 90, w: 170, title: 'asr/ear.py', subtitle: '能量门 · 有界队列 · 收流', tone: 'control' },
      { id: 'sauc', x: 435, y: 90, w: 170, title: '豆包 SAUC WebSocket', subtitle: '二进制协议 · partial / utterance', tone: 'external' },
      { id: 'listen', x: 40, y: 220, w: 165, h: 82, title: 'Asr + ListenerController', subtitle: 'ptt / always\nonset → stop → deliver', tone: 'agent' },
      { id: 'declare', x: 270, y: 230, w: 185, title: 'mindflow.declare', subtitle: 'FIRST / TAIL · 同 id 吸收', tone: 'agent' },
      { id: 'ghost', x: 490, y: 230, w: 120, title: 'Ghost / Shell', subtitle: 'CTML 字流', tone: 'agent' },
      { id: 'say', x: 680, y: 90, w: 270, title: 'say Routine', subtitle: 'body feed / eof finish · lane FIFO · mouth 锁', tone: 'runtime' },
      { id: 'tts', x: 680, y: 205, w: 270, title: 'SeedTTS（公共 tts 包）', subtitle: '文本队列 → Speaker → PCM audio chunks', tone: 'runtime' },
      { id: 'player', x: 680, y: 320, w: 270, title: 'LocalSpeaker', subtitle: 'PCM 对齐 → sounddevice · stop 截声', tone: 'runtime' },
      { id: 'output', x: 680, y: 435, w: 270, title: 'output · speech start / stop', subtitle: '出声闸门广播；不是外部语音总线', tone: 'data' },
    ],
    edges: [
      { from: 'mic', to: 'ear', label: 'PCM' },
      { from: 'ear', to: 'sauc', label: 'gzip 音频 / 识别事件', both: true },
      { from: 'sauc', to: 'listen', dashed: true, d: 'M 520 156 C 520 190, 122 180, 122 220', label: 'EarEvent', labelX: 335, labelY: 177 },
      { from: 'listen', to: 'declare', label: '申报' },
      { from: 'declare', to: 'ghost', label: '注意力赢家' },
      { from: 'ghost', to: 'say', label: 'say 标签 / body', d: 'M 610 261 C 650 261, 640 123, 680 123', labelX: 646, labelY: 202 },
      { from: 'say', to: 'tts', fromSide: 'bottom', toSide: 'top', label: 'feed / finish' },
      { from: 'tts', to: 'player', fromSide: 'bottom', toSide: 'top', label: 'PCM' },
      { from: 'say', to: 'output', dashed: true, d: 'M 950 123 C 978 123, 978 468, 950 468', label: 'publish', labelX: 968, labelY: 294 },
      { from: 'output', to: 'listen', dashed: true, d: 'M 680 468 C 500 530, 122 530, 122 302', label: 'mute_on_say · 半双工丢麦', labelX: 396, labelY: 510 },
    ],
  },
  pi: {
    title: 'Pi 包装层、单工具桥与持久 Python 环境', height: 560,
    caption: 'AgentSession 负责通用 Agent 机制；PiAgent 只负责装配、事件翻译和把唯一工具转发进 Routine 系统。',
    groups: [
      { x: 20, y: 48, w: 205, h: 455, label: '会话与资源', tone: 'storage' },
      { x: 245, y: 48, w: 320, h: 455, label: 'zero-ts · PiAgent', tone: 'agent' },
      { x: 585, y: 48, w: 395, h: 455, label: 'kernel 与 zero-py', tone: 'runtime' },
    ],
    nodes: [
      { id: 'bridge', x: 40, y: 90, w: 165, title: 'web_server', subtitle: 'prompt / abort / history', tone: 'external' },
      { id: 'session', x: 40, y: 200, w: 165, title: 'SessionManager', subtitle: 'pi-sessions/*.jsonl', tone: 'storage' },
      { id: 'resources', x: 40, y: 310, w: 165, h: 82, title: 'workspace', subtitle: '.pi/skills · AGENTS.md\n模型配置与文件缓存', tone: 'storage' },
      { id: 'resume', x: 40, y: 425, w: 165, title: 'resume', subtitle: '回放会话，不回放执行', tone: 'boundary' },
      { id: 'agent', x: 270, y: 90, w: 270, title: 'PiAgent Routine', subtitle: '初始化 · epoch 中断 · 生命周期', tone: 'agent' },
      { id: 'sdk', x: 270, y: 200, w: 270, title: 'AgentSession SDK', subtitle: '循环 · 压缩 · 重试 · steering', tone: 'agent' },
      { id: 'tool', x: 270, y: 320, w: 270, title: 'custom tool: ipython', subtitle: 'code / timeout / reset · AbortSignal', tone: 'control' },
      { id: 'events', x: 270, y: 425, w: 270, title: '事件翻译器', subtitle: 'assistant_output / feedback / usage', tone: 'control' },
      { id: 'call', x: 610, y: 90, w: 160, title: 'callStoppable', subtitle: '按名路由 + 停止信号', tone: 'runtime' },
      { id: 'ipython', x: 795, y: 90, w: 160, title: 'IPython.py', subtitle: '按 agent_id 复用内核', tone: 'runtime' },
      { id: 'namespace', x: 610, y: 220, w: 345, title: '持久用户命名空间', subtitle: '变量 / import / display / 超时中断 / reset', tone: 'runtime' },
      { id: 'runroutine', x: 610, y: 330, w: 345, title: 'run_routine()', subtitle: 'Python 环境中的显式桥接函数', tone: 'control' },
      { id: 'routines', x: 610, y: 425, w: 345, title: '共享 Routine 能力', subtitle: '设备、数据、DAG、其他 Agent；不新增工具 schema', tone: 'runtime' },
    ],
    edges: [
      { from: 'bridge', to: 'agent', label: '会话请求' },
      { from: 'agent', to: 'sdk', fromSide: 'bottom', toSide: 'top' },
      { from: 'sdk', to: 'tool', fromSide: 'bottom', toSide: 'top', label: '唯一模型工具' },
      { from: 'tool', to: 'call', label: 'execute', d: 'M 405 320 C 405 145, 540 123, 610 123', labelX: 496, labelY: 155 },
      { from: 'call', to: 'ipython' },
      { from: 'ipython', to: 'namespace', fromSide: 'bottom', toSide: 'top' },
      { from: 'namespace', to: 'runroutine', fromSide: 'bottom', toSide: 'top', label: '代码内调用' },
      { from: 'runroutine', to: 'routines', fromSide: 'bottom', toSide: 'top' },
      { from: 'session', to: 'sdk', dashed: true, d: 'M 205 233 C 235 233, 235 233, 270 233' },
      { from: 'resources', to: 'sdk', dashed: true, d: 'M 205 351 C 245 351, 230 260, 270 260' },
      { from: 'resume', to: 'session', fromSide: 'bottom', toSide: 'top', dashed: true },
      { from: 'sdk', to: 'events', dashed: true, d: 'M 540 250 C 570 250, 565 458, 540 458', labelX: 568, labelY: 350 },
      { from: 'events', to: 'bridge', dashed: true, d: 'M 270 458 C 230 458, 230 123, 205 123', labelX: 236, labelY: 280 },
      { from: 'ipython', to: 'tool', dashed: true, d: 'M 875 90 C 875 35, 405 35, 405 320', label: 'text + image blocks', labelX: 660, labelY: 30 },
    ],
  },
  xml: {
    title: 'XML Agent 的流式行动与在途输入仲裁', height: 600,
    caption: '每轮 react 只创建一个 Act；解析、说话和工具执行可以在模型字流尚未结束时发生。',
    groups: [
      { x: 20, y: 48, w: 210, h: 495, label: '输入与反射', tone: 'external' },
      { x: 250, y: 48, w: 340, h: 495, label: 'XmlAgent · 常驻编排', tone: 'agent' },
      { x: 610, y: 48, w: 370, h: 495, label: 'Act · 本轮行动子树', tone: 'runtime' },
    ],
    nodes: [
      { id: 'input', x: 40, y: 90, w: 170, title: '用户 / 语音 / 视觉', subtitle: 'web_server · chat_message', tone: 'external' },
      { id: 'reflex', x: 40, y: 210, w: 170, h: 82, title: 'ReflexChain', subtitle: 'state_gate + Jev\nPASS / INTERRUPT / DEFER', tone: 'control' },
      { id: 'deferred', x: 40, y: 340, w: 170, title: 'deferred / pending', subtitle: '合并重判；drain 后开新轮', tone: 'boundary' },
      { id: 'stop', x: 40, y: 450, w: 170, title: 'interrupt / epoch', subtitle: '拒绝陈旧流；停当前子树', tone: 'boundary' },
      { id: 'agent', x: 275, y: 90, w: 290, title: 'XmlAgent', subtitle: '_on_input → react → 当前 Act', tone: 'agent' },
      { id: 'memory', x: 275, y: 205, w: 290, title: 'XmlContextProvider', subtitle: 'SQLite Memory + OpenViking + workspace', tone: 'storage' },
      { id: 'prompt', x: 275, y: 315, w: 290, title: '上下文装配', subtitle: '历史 / Skill / Routine 白名单与别名', tone: 'agent' },
      { id: 'llm', x: 275, y: 435, w: 290, title: 'LLMClient', subtitle: 'Responses API · 流式 TextDelta', tone: 'external' },
      { id: 'act', x: 635, y: 90, w: 320, title: 'Act（每轮一个）', subtitle: 'body queue · _eof · async generator', tone: 'runtime' },
      { id: 'parser', x: 635, y: 205, w: 320, title: 'XmlRoutine + XML parser', subtitle: 'TextChunk / ChildOpen / Body / Close', tone: 'control' },
      { id: 'shell', x: 635, y: 315, w: 320, title: 'body_shell', subtitle: '标签 → create / body / close；别名映射回注册名', tone: 'control' },
      { id: 'children', x: 635, y: 435, w: 320, title: 'speak / car/* / 其他 Routine', subtitle: '完成回调 → done_q → 流式返回 Agent', tone: 'runtime' },
    ],
    edges: [
      { from: 'input', to: 'agent', label: '空闲：直接开轮' },
      { from: 'input', to: 'reflex', fromSide: 'bottom', toSide: 'top', label: 'Act 在途', labelX: 112, labelY: 180 },
      { from: 'reflex', to: 'deferred', fromSide: 'bottom', toSide: 'top', label: '延后 / 待判' },
      { from: 'reflex', to: 'stop', dashed: true, d: 'M 125 292 C 125 380, 125 450, 125 450', label: '打断', labelX: 150, labelY: 390 },
      { from: 'agent', to: 'memory', fromSide: 'bottom', toSide: 'top', dashed: true, both: true },
      { from: 'memory', to: 'prompt', fromSide: 'bottom', toSide: 'top' },
      { from: 'prompt', to: 'llm', fromSide: 'bottom', toSide: 'top' },
      { from: 'llm', to: 'act', label: '字流 ctx.send', d: 'M 565 468 C 610 468, 600 123, 635 123', labelX: 604, labelY: 315 },
      { from: 'act', to: 'parser', fromSide: 'bottom', toSide: 'top' },
      { from: 'parser', to: 'shell', fromSide: 'bottom', toSide: 'top' },
      { from: 'shell', to: 'children', fromSide: 'bottom', toSide: 'top' },
      { from: 'children', to: 'act', dashed: true, d: 'M 955 468 C 990 468, 990 123, 955 123', label: 'on_stopped → done_q', labelX: 968, labelY: 300 },
      { from: 'stop', to: 'act', dashed: true, label: 'cancel react + stop Act', d: 'M 210 483 C 440 483, 500 123, 635 123', labelX: 438, labelY: 468 },
    ],
  },
  moss: {
    title: 'Moss 的注意力、思考帧与行动回环', height: 650,
    caption: 'declare 只登记；Attention 决定谁开拍；Moment 缝合历史；只有 next 或 owed 观测能显式推动续拍。',
    groups: [
      { x: 20, y: 48, w: 205, h: 545, label: '多源感知', tone: 'external' },
      { x: 245, y: 48, w: 250, h: 545, label: 'Mindflow · 调度', tone: 'agent' },
      { x: 515, y: 48, w: 215, h: 545, label: 'Ghost · 一拍', tone: 'agent' },
      { x: 750, y: 48, w: 230, h: 545, label: 'Shell · 行动', tone: 'runtime' },
    ],
    nodes: [
      { id: 'sources', x: 40, y: 90, w: 165, title: 'ASR / UI / 设备', subtitle: '文本、状态、首包与尾包', tone: 'external' },
      { id: 'declare', x: 40, y: 205, w: 165, title: 'mindflow.declare', subtitle: 'priority / strength / mode', tone: 'control' },
      { id: 'command', x: 40, y: 325, w: 165, title: 'command', subtitle: 'CTML 直投，不经思考', tone: 'control' },
      { id: 'transient', x: 40, y: 445, w: 165, title: 'transient', subtitle: '同源覆盖 · 永不入史', tone: 'storage' },
      { id: 'pending', x: 270, y: 90, w: 200, title: 'Nuclei + pending', subtitle: '治理 · 聚合 · 检录缓冲', tone: 'agent' },
      { id: 'attention', x: 270, y: 205, w: 200, h: 82, title: 'rank + challenge', subtitle: 'Attention 保护期\n强度衰减 · 抢占 / 吸收', tone: 'agent' },
      { id: 'chain', x: 270, y: 345, w: 200, title: '拍链', subtitle: '单源成链 · next 自续', tone: 'agent' },
      { id: 'interrupt', x: 270, y: 475, w: 200, title: 'interrupt / abort', subtitle: '停身体与答不答相互独立', tone: 'boundary' },
      { id: 'ghost', x: 535, y: 90, w: 175, title: 'Ghost.think', subtitle: '一次请求 = 一个 Moment', tone: 'agent' },
      { id: 'moments', x: 535, y: 205, w: 175, h: 88, title: 'Moments', subtitle: 'previous · percepts\ndynamic_context · logos', tone: 'storage' },
      { id: 'brain', x: 535, y: 350, w: 175, title: 'Brain / LLM', subtitle: 'input_items 渲染 · 字流', tone: 'external' },
      { id: 'epoch', x: 535, y: 480, w: 175, title: '滑窗 + Epoch', subtitle: '帧链 → recap；瞬态分离', tone: 'storage' },
      { id: 'shell', x: 770, y: 90, w: 190, title: 'BodyScanner', subtitle: 'CTML · 一个标签一个实例', tone: 'runtime' },
      { id: 'route', x: 770, y: 205, w: 190, title: 'route + Channel', subtitle: '词表闸门 · lane · all/any/loop', tone: 'control' },
      { id: 'actions', x: 770, y: 325, w: 190, title: '动作 Routine', subtitle: 'say / car/* / code / board', tone: 'runtime' },
      { id: 'tracer', x: 770, y: 460, w: 190, h: 82, title: 'Tracer + obs', subtitle: '谁投递谁收 · 迟到回执\n错误 / meta / observe = owed', tone: 'boundary' },
    ],
    edges: [
      { from: 'sources', to: 'declare', fromSide: 'bottom', toSide: 'top' },
      { from: 'declare', to: 'pending', label: '登记，不直接开脑' },
      { from: 'pending', to: 'attention', fromSide: 'bottom', toSide: 'top' },
      { from: 'attention', to: 'chain', fromSide: 'bottom', toSide: 'top', label: '赢家' },
      { from: 'chain', to: 'ghost', label: 'think' },
      { from: 'ghost', to: 'moments', fromSide: 'bottom', toSide: 'top', dashed: true, both: true },
      { from: 'moments', to: 'brain', fromSide: 'bottom', toSide: 'top', label: 'feed' },
      { from: 'brain', to: 'shell', label: 'CTML 字流' },
      { from: 'shell', to: 'route', fromSide: 'bottom', toSide: 'top' },
      { from: 'route', to: 'actions', fromSide: 'bottom', toSide: 'top' },
      { from: 'actions', to: 'tracer', fromSide: 'bottom', toSide: 'top', label: '回执' },
      { from: 'tracer', to: 'moments', dashed: true, d: 'M 770 501 C 720 501, 745 249, 710 249', label: 'obs 入史', labelX: 741, labelY: 400 },
      { from: 'tracer', to: 'chain', dashed: true, d: 'M 770 520 C 650 610, 470 520, 470 378', label: 'owed 才续拍', labelX: 624, labelY: 577 },
      { from: 'command', to: 'route', dashed: true, d: 'M 205 358 C 510 358, 620 238, 770 238', label: '反射弧', labelX: 520, labelY: 340 },
      { from: 'transient', to: 'moments', dashed: true, d: 'M 205 478 C 390 478, 450 275, 535 275', label: '当前快照', labelX: 398, labelY: 460 },
      { from: 'interrupt', to: 'route', dashed: true, d: 'M 470 508 C 640 508, 680 270, 770 270', label: 'shell.abort', labelX: 642, labelY: 492 },
    ],
  },
  dag: {
    title: 'DAG 静态校验与就绪集调度器', height: 590,
    caption: '调度单位是“依赖已经终态的节点”，不是拓扑层；子实例完成事件驱动下一轮 try_dispatch。',
    groups: [
      { x: 20, y: 48, w: 215, h: 485, label: '定义与校验', tone: 'storage' },
      { x: 255, y: 48, w: 330, h: 485, label: 'RunDag · 调度循环', tone: 'control' },
      { x: 605, y: 48, w: 375, h: 485, label: 'Routine 子实例与观测', tone: 'runtime' },
    ],
    nodes: [
      { id: 'yaml', x: 40, y: 90, w: 175, title: 'DAG YAML', subtitle: 'nodes / depends_on / outputs', tone: 'storage' },
      { id: 'spec', x: 40, y: 205, w: 175, h: 82, title: 'loader → DagSpec', subtitle: '未知引用 · Kahn 环检测\n输入类型与 trigger_rule', tone: 'control' },
      { id: 'inputs', x: 40, y: 345, w: 175, title: 'inputs JSON', subtitle: '必填校验与类型强制转换', tone: 'storage' },
      { id: 'nested', x: 40, y: 450, w: 175, title: 'run_dag 节点', subtitle: 'DAG 可以嵌套为普通子实例', tone: 'runtime' },
      { id: 'ready', x: 280, y: 90, w: 280, title: 'pending + running + outputs', subtitle: 'deps_all_terminal → 就绪节点', tone: 'control' },
      { id: 'resolve', x: 280, y: 205, w: 280, title: 'resolve_kwargs + should_run', subtitle: '$node.output / $WI.input / when / trigger_rule', tone: 'control' },
      { id: 'dispatch', x: 280, y: 320, w: 280, title: 'try_dispatch', subtitle: 'create → 挂 on_stopped → start；立即继续扫描', tone: 'control' },
      { id: 'done', x: 280, y: 435, w: 280, title: 'done_q → 状态迁移', subtitle: 'completed / failed / skipped / retrying', tone: 'control' },
      { id: 'branches', x: 630, y: 90, w: 325, title: '独立就绪分支并发', subtitle: 'A ─┐\nB ─┼→ 下游汇合（示意结构）', tone: 'runtime' },
      { id: 'events', x: 630, y: 220, w: 325, title: 'dag_event', subtitle: 'run_started / node_event / run_done', tone: 'data' },
      { id: 'retry', x: 630, y: 325, w: 325, title: '失败处理', subtitle: '指数退避重试；失败与跳过也可解锁指定规则下游', tone: 'boundary' },
      { id: 'cancel', x: 630, y: 445, w: 325, title: 'cancel 哨兵与结果组装', subtitle: '停止继续派发；运行中节点标 skipped，父停止负责级联回收', tone: 'boundary' },
    ],
    edges: [
      { from: 'yaml', to: 'spec', fromSide: 'bottom', toSide: 'top' },
      { from: 'spec', to: 'ready', label: '静态结构先于执行' },
      { from: 'inputs', to: 'resolve', label: '外部输入' },
      { from: 'ready', to: 'resolve', fromSide: 'bottom', toSide: 'top' },
      { from: 'resolve', to: 'dispatch', fromSide: 'bottom', toSide: 'top' },
      { from: 'dispatch', to: 'branches', label: 'create + start' },
      { from: 'branches', to: 'done', dashed: true, d: 'M 792 156 C 792 300, 560 300, 560 468', label: 'on_stopped', labelX: 677, labelY: 285 },
      { from: 'done', to: 'ready', dashed: true, d: 'M 280 468 C 245 468, 245 123, 280 123', label: '解锁下游', labelX: 248, labelY: 300 },
      { from: 'done', to: 'events', label: '发布状态' },
      { from: 'done', to: 'retry', label: 'failed', d: 'M 560 468 C 600 468, 590 358, 630 358', labelX: 594, labelY: 450 },
      { from: 'retry', to: 'ready', dashed: true, d: 'M 792 325 C 792 40, 420 40, 420 90', label: '重新入 pending', labelX: 612, labelY: 36 },
      { from: 'done', to: 'cancel', fromSide: 'bottom', toSide: 'left', label: '终态 / 哨兵' },
      { from: 'nested', to: 'branches', dashed: true, label: '作为普通节点' },
    ],
  },
  skills: {
    title: 'Skill 共享库、工作区与渐进披露', height: 580,
    caption: 'SkillRegistry 扫描内容包，RoutinesLoader 注册管理 Routine；两套注册语义相邻但不合并。',
    groups: [
      { x: 20, y: 48, w: 205, h: 475, label: '内容来源与共享库', tone: 'storage' },
      { x: 245, y: 48, w: 315, h: 475, label: 'Agent 工作区与上下文', tone: 'agent' },
      { x: 580, y: 48, w: 400, h: 475, label: 'Routine 执行面', tone: 'runtime' },
    ],
    nodes: [
      { id: 'fetch', x: 40, y: 90, w: 165, title: 'skill/fetch', subtitle: 'URL → 文件包', tone: 'external' },
      { id: 'library', x: 40, y: 200, w: 165, h: 82, title: '~/.zero/skills', subtitle: '跨 Agent 单一真源\nSKILL.md + 资源文件', tone: 'storage' },
      { id: 'registry', x: 40, y: 340, w: 165, h: 88, title: 'SkillRegistry', subtitle: '多目录 first-wins\nfrontmatter · agents 过滤', tone: 'control' },
      { id: 'publish', x: 40, y: 455, w: 165, title: 'repo/publish', subtitle: '工作区版本发布回共享库', tone: 'control' },
      { id: 'pull', x: 270, y: 90, w: 265, title: 'skill/repo/pull', subtitle: '按 agent/state 找 skill_dir；同版本默认跳过', tone: 'control' },
      { id: 'workspace', x: 270, y: 200, w: 265, title: 'workspace/skills', subtitle: '当前 Agent 可用副本；允许本地改动', tone: 'storage' },
      { id: 'levels', x: 270, y: 315, w: 265, h: 88, title: '两种进入上下文的方式', subtitle: 'summary：名称与描述后按需 load\nfull：创建时并入 system prompt', tone: 'agent' },
      { id: 'load', x: 270, y: 445, w: 265, title: 'skill/list + skill/load', subtitle: '正文作为 tool result 返回，保留推理因果链', tone: 'agent' },
      { id: 'pi', x: 605, y: 90, w: 350, title: 'Pi / XML Agent', subtitle: '读取指令，决定下一步行动；Registry 无会话状态', tone: 'agent' },
      { id: 'bridge', x: 605, y: 225, w: 350, title: 'routine-bridge / IPython', subtitle: '在 Python 环境内显式调用 run_routine', tone: 'control' },
      { id: 'routines', x: 605, y: 345, w: 350, title: '共享 Routine 系统', subtitle: '真实设备、数据与编排能力', tone: 'runtime' },
      { id: 'loader', x: 605, y: 455, w: 350, title: 'RoutinesLoader', subtitle: '只负责注册 skill/list、skill/load、repo/* 等 Routine', tone: 'boundary' },
    ],
    edges: [
      { from: 'fetch', to: 'library', fromSide: 'bottom', toSide: 'top' },
      { from: 'library', to: 'registry', fromSide: 'bottom', toSide: 'top' },
      { from: 'library', to: 'pull', label: '复制到工作区' },
      { from: 'pull', to: 'workspace', fromSide: 'bottom', toSide: 'top' },
      { from: 'workspace', to: 'levels', fromSide: 'bottom', toSide: 'top' },
      { from: 'levels', to: 'load', fromSide: 'bottom', toSide: 'top', dashed: true },
      { from: 'levels', to: 'pi', label: 'prompt / tool result' },
      { from: 'load', to: 'pi', dashed: true, d: 'M 402 445 C 402 535, 780 535, 780 156', labelX: 593, labelY: 540 },
      { from: 'pi', to: 'bridge', fromSide: 'bottom', toSide: 'top' },
      { from: 'bridge', to: 'routines', fromSide: 'bottom', toSide: 'top' },
      { from: 'loader', to: 'pull', dashed: true, d: 'M 605 488 C 560 488, 575 123, 535 123', label: '注册管理入口', labelX: 566, labelY: 320 },
      { from: 'registry', to: 'levels', dashed: true, label: '扫描摘要 / 读取正文' },
      { from: 'publish', to: 'library', fromSide: 'top', toSide: 'bottom', dashed: true, label: '版本回流', labelX: 153, labelY: 320 },
    ],
  },
}
</script>

<template>
  <figure class="architecture-figure">
    <div class="architecture-head">
      <div><span class="mono">ARCHITECTURE</span><h3>{{ diagram.title }}</h3></div>
      <div class="architecture-legend" aria-label="图例">
        <span><i class="legend-solid"></i>调用 / 数据</span><span><i class="legend-dashed"></i>反馈 / 热路径</span>
      </div>
    </div>
    <div class="architecture-scroll">
      <svg
        class="architecture-svg"
        :viewBox="`0 0 1000 ${diagram.height}`"
        role="img"
        :aria-label="`${diagram.title}。${diagram.caption}`"
      >
        <defs>
          <marker :id="markerId" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" class="arrow-head" />
          </marker>
        </defs>
        <g v-for="group in diagram.groups" :key="group.label">
          <rect :x="group.x" :y="group.y" :width="group.w" :height="group.h" rx="10" :class="['group-box', `tone-${group.tone || 'control'}`]" />
          <text :x="group.x + 16" :y="group.y + 25" class="group-label">{{ group.label }}</text>
        </g>
        <g v-for="edge in diagram.edges" :key="`${edge.from}-${edge.to}-${edge.label || ''}`" class="edge">
          <path
            :d="edgeGeometry(edge).d"
            :class="{ dashed: edge.dashed }"
            :marker-end="`url(#${markerId})`"
            :marker-start="edge.both ? `url(#${markerId})` : undefined"
          />
          <text v-if="edge.label" :x="edgeGeometry(edge).x" :y="edgeGeometry(edge).y" text-anchor="middle">{{ edge.label }}</text>
        </g>
        <g v-for="node in diagram.nodes" :key="node.id">
          <rect
            :x="node.x" :y="node.y" :width="node.w || 150" :height="node.h || 66" rx="7"
            :class="['node-box', `tone-${node.tone || 'control'}`]"
          />
          <text :x="node.x + 13" :y="node.y + 25" class="node-title">{{ node.title }}</text>
          <text v-if="node.subtitle" :x="node.x + 13" :y="node.y + 45" class="node-subtitle">
            <tspan v-for="(line, index) in lines(node.subtitle)" :key="line" :x="node.x + 13" :dy="index ? 16 : 0">{{ line }}</tspan>
          </text>
        </g>
      </svg>
    </div>
    <figcaption>{{ diagram.caption }}</figcaption>
  </figure>
</template>

<style scoped>
.architecture-figure { margin: 26px 0 30px; border: 1px solid #dbe2eb; border-radius: 8px; background: #fff; overflow: hidden; }
.architecture-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; padding: 18px 20px 15px; border-bottom: 1px solid #e6eaf0; }
.architecture-head > div:first-child > span { color: #718096; font-size: 8px; letter-spacing: .12em; }
.architecture-head h3 { margin-top: 5px; color: #253247; font-size: 15px; font-weight: 650; }
.architecture-legend { display: flex; flex-wrap: wrap; gap: 12px; color: #7b8797; font-size: 9px; white-space: nowrap; }
.architecture-legend span { display: inline-flex; align-items: center; gap: 5px; }
.architecture-legend i { display: inline-block; width: 18px; border-top: 1.5px solid #7d8ba0; }
.architecture-legend .legend-dashed { border-top-style: dashed; }
.architecture-scroll { overflow-x: auto; background: #f8fafc; }
.architecture-svg { display: block; width: 100%; min-width: 780px; height: auto; }
.group-box { stroke-width: 1; stroke-dasharray: 4 4; }
.group-box.tone-external { fill: #f4f7fa; stroke: #d6dee8; }
.group-box.tone-control { fill: #f5f6fb; stroke: #dcdfea; }
.group-box.tone-runtime { fill: #f3f8f7; stroke: #d4e4e0; }
.group-box.tone-agent { fill: #f6f4fa; stroke: #e0d9eb; }
.group-box.tone-storage { fill: #faf8f2; stroke: #e8e0cd; }
.group-label { fill: #788698; font-family: var(--font-mono); font-size: 10px; letter-spacing: .035em; }
.node-box { stroke-width: 1.15; }
.node-box.tone-control { fill: #fff; stroke: #9ba9c1; }
.node-box.tone-runtime { fill: #fff; stroke: #78a99c; }
.node-box.tone-agent { fill: #fff; stroke: #a494bd; }
.node-box.tone-storage { fill: #fffdfa; stroke: #c4ad75; }
.node-box.tone-external { fill: #fff; stroke: #9aa8b7; }
.node-box.tone-data { fill: #f0f6fb; stroke: #78a4c4; }
.node-box.tone-boundary { fill: #fffaf1; stroke: #cba75e; }
.node-title { fill: #263449; font-family: var(--font-sans); font-size: 12px; font-weight: 650; }
.node-subtitle { fill: #68768a; font-family: var(--font-sans); font-size: 9.5px; font-weight: 400; }
.edge path { fill: none; stroke: #8291a6; stroke-width: 1.25; }
.edge path.dashed { stroke: #9a8aaf; stroke-dasharray: 5 4; }
.edge text { fill: #64748a; stroke: #f8fafc; stroke-width: 4px; paint-order: stroke; font-family: var(--font-sans); font-size: 9px; }
.arrow-head { fill: #8291a6; }
figcaption { padding: 12px 20px 14px; border-top: 1px solid #e6eaf0; color: #66758a; font-size: 11px; line-height: 1.75; }
@media (max-width: 650px) {
  .architecture-head { align-items: flex-start; flex-direction: column; gap: 10px; padding: 15px 16px 12px; }
  .architecture-legend { white-space: normal; }
  .architecture-svg { min-width: 760px; }
  figcaption { padding: 11px 16px 13px; }
}
</style>
