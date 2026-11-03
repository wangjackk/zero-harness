/* 拓扑骨架：7 条证据带，54 个点（同名族折叠，xN 表示这个点里收着几个名字）。
   一个点必须对得上表里的键 —— 要么一个注册名，要么一族同前缀的名字。
   **类别名不进图**（早先有一块写的是「控制原语」，那是个归类，不是键）。
   出处：py 侧 123 个注册名（探针导出的 registry.json）+ ts 侧 9 个 + rust 侧 6 个。
   **名字是全语言同一张表**：pi/* 由 ts 的 hub 拨进来，music/* 由 rust 的 hub 拨进来，
   落到同一个 kernel 里，跟 py 的名字抢同一个命名空间。所以它们在图上和 py 的点并排，
   只是多一个语言角标 —— 语言是实现细节，名字才是表里的键。 */

export type Proc = 'z' | 'm' | 'b' | 's'
/* hub 只有三个，判据是「它往表里放了名字」。go 有 SDK 但没有注册名 —— 没有名字就没有位置。 */
export type Impl = 'py' | 'ts' | 'rs'
export type BandId = 'meta' | 'agent' | 'lang' | 'tools' | 'body' | 'sense' | 'prompt'

export interface Chip {
  n: string        // 注册名（标签面写法）
  p: Proc
  f: string        // 文件路径（py 相对 zero-py/，其余写完整相对路径）
  d: string        // 它是什么
  w?: string       // 为什么它也算 routine（只写最反直觉的）
  x?: number       // 同名族折叠数
  impl?: Impl      // 缺省 py。写了就必须能指出是哪个 hub 注册的
}

export interface Band {
  id: BandId
  no: string
  title: string
  sub: string
  accent: string
  chips: Chip[]
}

export const BANDS: Band[] = [
  {
    id: 'meta', no: '①', title: '元能力 · 系统管理', sub: '注册、查询与重载', accent: '#F0B24A',
    chips: [
      { n: 'routines_loader', p: 's', f: 'app/routines/loader.py',
        d: '唯一静态注册的 passive 单元。由 kernel 启动后读取清单、import 并注册其余单元，完成后退出。',
        w: '引导单元与其他单元采用相同的注册形式；系统没有独立的框架启动代码。' },
      { n: 'routines_watcher', p: 'b', f: 'app/routines/watcher.py',
        d: '检查 routines.yaml 与源码 mtime 变化并自动热重载（HMR），无需重启进程。',
        w: '热重载由可停止的单元实现，不依赖宿主进程的特殊机制。' },
      { n: 'routine/list', p: 'z', f: 'app/routines/user/routine/list.py',
        d: '列出单元：kind=registered（注册名 → 类）| running（运行中的命令实例）。',
        w: '能力清单查询本身也需注册后调用。' },
      { n: 'routine/doc', p: 'b', f: 'app/routines/user/routine/doc.py',
        d: '读取单个单元的 name / schema / doc；唯一事实源为 Routine 类声明。' },
      { n: 'routine/reload', p: 'z', f: 'app/routines/user/routine/reload.py',
        d: '强制重载并重新注册单元或整个包；用于 watcher 未能完成重载时手动恢复。' },
      { n: 'routine/check_name', p: 'z', f: 'app/routines/user/routine/check_name.py',
        d: '检查注册名是否可用。同名热重载会替换现有路由，调用前需检查注册名。' }
    ]
  },
  {
    id: 'agent', no: '②', title: 'Agent · 决策与身份', sub: '决策、实例与身份管理', accent: '#A98BFA',
    chips: [
      { n: 'user_agent', p: 'z', f: 'app/routines/user/user_agent.py',
        d: 'passive 常驻的身份单元：前端消息先按 X-Agent-Id 解析到其 rid。' },
      { n: 'world_agent', p: 'z', f: 'app/routines/user/world_agent.py',
        d: '世界侧的常驻身份单元。与 user_agent 同构，仅身份不同。' },
      { n: 'xml/agent', p: 'z', f: 'app/routines/user/xml/agent/agent.py',
        d: '精简 reactive agent：使用 sqlite 记忆，每轮 push 一个 act，保持常驻对话。',
        w: 'Agent 与工具均为单元；区别在于 Agent 可继续调用后续步骤。' },
      { n: 'xml/agent/manager', p: 'z', f: 'app/routines/user/xml/agent/manager.py',
        d: 'Agent 命令实例管理（passive 常驻）：create / list / stop / resume / delete / set_routine_alias。' },
      { n: 'mini_agent', p: 'z', f: 'app/routines/user/dynamic/mini_agent.py',
        d: '轻量子 Agent：接收 task+role，驱动 LLM 循环，并按需调用白名单中的单元。' },
      { n: 'xml_condenser_agent', p: 'z', f: 'app/routines/user/agents/_core/condenser/routine.py',
        d: '上下文压缩 Agent：将长历史压缩为摘要。',
        w: '上下文压缩可调度；底层存储不属于单元（见反例）。' },
      { n: 'agent_hub', p: 'z', f: 'app/routines/user/agent_hub.py',
        d: 'Agent 类型注册中心（passive 常驻 hub）：各类型 manager 启动时通过 register 提交 manifest。它也有注册名，因此可通过调用查询可用的 Agent 实现，而非读取框架常量。' },
      { n: 'pi/agent', p: 'z', impl: 'ts', f: 'zero-ts/app/routines/user/agents/pi/agent.ts',
        d: 'TypeScript 侧的 pi 引擎：唯一工具是 ipython，该调用经 kernel 路由到 py 侧同名 ipython。',
        w: '跨语言与本地调用使用相同的 req 注册名。' },
      { n: 'pi/agent/*', p: 'z', impl: 'ts', x: 6, f: 'zero-ts/app/routines/user/agents/pi/manager.ts',
        d: 'manager（常驻）+ create / stop / resume / list / delete：Agent 命令实例的完整生命周期，每个操作对应一个注册名。' },
      { n: 'pi/settings/*', p: 'z', impl: 'ts', x: 2, f: 'zero-ts/app/routines/user/agents/pi/settings.ts',
        d: '读写 pi 引擎的模型与参数；切换模型通过调用完成，无需修改配置并重启。' },
      { n: 'ghost', p: 'm', f: 'app/routines/user/mossv2/ghost.py',
        d: '输入与动作之间的 broker：接收输入、调度节拍、返回回执。',
        w: '载体不是内核对象，可通过 routine/list 查询其命令实例。' },
      { n: 'mindflow', p: 'm', f: 'app/routines/user/mossv2/mindflow.py',
        d: '决策仲裁：每个节拍根据强度衰减、优先级和待处理记录决定是否响应、抢占或停止。' },
      { n: 'moss', p: 'm', f: 'app/routines/user/mossv2/moss.py',
        d: '语音 Agent 的装配根：组合 mindflow / ghost / board / say / asr（清单中必须最后注册）。',
        w: '它注册到 agent hub，作为一种 Agent 实现；完整 Agent 的装配也通过单元调用完成。' }
    ]
  },
  {
    id: 'lang', no: '③', title: '编排与语言', sub: '解析、容器与调度', accent: '#3ED2E0',
    chips: [
      { n: 'shell', p: 'm', f: 'app/routines/user/mossv2/shell.py',
        d: '解析标签、按注册名生成调用，并由容器执行 defer/release。文本解析与投递在此完成。',
        w: '解析器同为单元，可替换并重载。' },
      { n: 'code', p: 'm', f: 'app/routines/user/mossv2/commands/code/code.py',
        d: '第四种容器：body 为 Python 源码，在常驻 CodeKernel 中执行（代码中不可使用容器）。' },
      { n: 'all', p: 'm', f: 'app/routines/user/mossv2/container.py', d: '时序容器：body 全部完成后继续当前轨道。' },
      { n: 'any', p: 'm', f: 'app/routines/user/mossv2/container.py', d: '时序容器：任一完成即继续当前轨道，并取消其余任务（竞速）。' },
      { n: 'loop', p: 'm', f: 'app/routines/user/mossv2/container.py', d: '循环容器：反复执行 body，直到收到停止指令。' },
      { n: 'chan/*', p: 'm', x: 5, f: 'app/routines/user/mossv2/channel.py + commands/chan/',
        d: 'channel（动作串行轨道）+ create / delete / clear / status。默认仅有 main 串行轨，新建轨道后才并行执行。',
        w: '并行度可由模型通过调用申请，无需修改配置。' },
      /* 原来是折叠成一块「控制原语」——那是**类别名**，不是名字。这张图上一个方块
         必须对得上表里的一个键（折叠族至少得是同前缀，`web_*` 那种）。拆成四个。 */
      { n: 'interrupt', p: 'm', f: 'app/routines/user/mossv2/commands/control.py',
        d: '中断当前动作轨，不生成回答；是否响应由 effort 单独决定。' },
      { n: 'notify', p: 'm', f: 'app/routines/user/mossv2/commands/notify.py',
        d: '通知登记：默认 BACKGROUND + mode=notify；已有当前任务时参与仲裁，未获执行时记入待处理。' },
      { n: 'sleep', p: 'm', f: 'app/routines/user/mossv2/commands/sleep.py',
        d: '当前轨道等待指定时长，其他轨道继续执行。' },
      { n: 'noop', p: 'm', f: 'app/routines/user/mossv2/commands/noop.py',
        d: '空操作：不执行动作，作为容器可等待的叶子节点。' }
    ]
  },
  {
    id: 'tools', no: '④', title: '工具与技能', sub: '与 Agent 共用注册表', accent: '#6BA5FF',
    chips: [
      { n: 'web_*', p: 'z', x: 2, f: 'app/routines/user/agents/tools/remote/',
        d: 'web_search / web_fetch：检索与获取网页，返回 tool result。' },
      { n: 'ssh/*', p: 'z', x: 5, f: 'app/routines/user/agents/tools/remote/SshTool/',
        d: 'connect / exec / transfer / list / disconnect。远端操作有独立接口，不通过本地 exec 扩展实现。' },
      { n: 'ipython', p: 'z', f: 'app/routines/user/ipython/IPython.py',
        d: '持久执行环境，变量跨轮保留，供 Agent 执行代码。',
        w: '它与只读图像的 read_image 同为单元；权限差异由审批机制控制，而非类型。' },
      { n: 'read_image', p: 'b', f: 'app/routines/user/xml/read_image.py',
        d: '读取本地图片并生成多模态载荷（images 随回执返回模型上下文）。' },
      { n: 'skill/*', p: 'z', x: 8, f: 'app/routines/user/skill/',
        d: '技能库：list 仅返回注册名与简介，load 注入全文（Progressive Disclosure）；repo/* 负责仓库新增、删除与拉取。',
        w: '技能由此组单元提供；新增技能只需增加单元，无需修改框架。' },
      { n: 'screenshot', p: 'z', f: 'app/routines/user/dynamic/screenshot.py',
        d: '将屏幕截图保存为文件，可由 read_image 读取。' },
      { n: 'approval_gate', p: 'z', f: 'app/routines/user/dynamic/approval_gate.py',
        d: '危险操作的人工审批：阻塞等待人工决定，拒绝或超时时均 fail-closed。',
        w: '通过强制调用审批单元实施约束，不依赖提示词约束。' }
    ]
  },
  {
    id: 'body', no: '⑤', title: '动作与表达', sub: '动作、语音与画板', accent: '#46D8A0',
    chips: [
      { n: 'say', p: 'm', f: 'app/routines/user/mossv2/commands/say.py', d: '语音输出（按 key 选择有声或静音）。' },
      { n: 'speak', p: 'm', impl: 'rs', f: 'zero-rs/src/routines/speak/',
        d: 'Rust 侧语音合成：输入文本，输出音频流。py 侧 say 选择有声输出时调用此注册名。',
        w: 'py 的 say 与 Rust 的 speak 共用注册表；切换实现语言不要求修改调用方。' },
      { n: 'audio/output/*', p: 'm', impl: 'rs', x: 2, f: 'zero-rs/src/routines/audio/',
        d: 'list / set：设备枚举与输出通道选择。' },
      { n: 'music/*', p: 'm', impl: 'rs', x: 3, f: 'zero-rs/src/routines/music/',
        d: 'list / play / sound_effect：曲库与音效。实时音频由 Rust 实现，以降低播放抖动。' },
      { n: 'ask', p: 'z', f: 'app/routines/user/ask.py',
        d: '向用户发送单选题或日期选择，并阻塞等待结果。',
        w: 'UI 交互同为单元；询问与移动指令使用相同的调用方式。' },
      { n: 'show_image', p: 'z', f: 'app/routines/user/show_image.py', d: '本地图片经 WS 推送到前端对话栏。' },
      { n: 'board', p: 'm', f: 'app/routines/user/mossv2/board/routine.py', d: '画板功能的单元接口（底层画板对象不在词表中）。' },
      { n: 'bb/*', p: 'm', x: 13, f: 'app/routines/user/mossv2/commands/board/',
        d: '提供 write / pixel / line / circle / ring / rect / poly / point / arrow / underline / erase / objects 等 13 个注册名。',
        w: '仅 write 与 pixel 接收 body；画板静默处理错误，由单元返回错误信息。' },
      { n: 'car/*', p: 'b', x: 14, f: 'app/routines/user/car/',
        d: 'move / turn / steer / shake / stop / status / where / map / navigate / manage / track / gaze / wake_track / snap。',
        w: '10Hz 控制节拍、代际互斥与零速保证均在此层实现；设备动作通过单元调用执行。' }
    ]
  },
  {
    id: 'sense', no: '⑥', title: '感知与设备', sub: '输入、事件与设备桥接', accent: '#F87093',
    chips: [
      { n: 'asr', p: 'm', f: 'app/routines/user/mossv2/asr/routine.py',
        d: '语音识别：音频流 → 文本 → moment，结果仅通过 mindflow.declare 输出。' },
      { n: 'asr_mode', p: 'm', f: 'app/routines/user/mossv2/commands/asr_mode.py', d: '麦克风开关策略：ptt / always（不跨重启）。' },
      { n: 'key_listen', p: 'm', f: 'app/routines/user/mossv2/key_listen.py', d: '全局键盘监听（pynput），作为 ptt 的物理输入入口。' },
      { n: 'bus_listener', p: 'b', f: 'app/routines/user/device_bus/listener.py',
        d: '设备事件总线：低频 evt 订阅 → pubsub voice/vision；将 pose、snap 等语义 req 转为 vision cmd。',
        w: '总线也是常驻单元，其他单元可对其发起 req 或重载。' },
      { n: 'ros_bridge', p: 'b', f: 'app/routines/user/hiwonder/ros_bridge.py',
        d: '感知桥接：动态订阅 ROS 话题并转发到同名 pubsub，也支持反向 publish。' }
    ]
  },
  {
    id: 'prompt', no: '⑦', title: '提示词', sub: '分段生成系统提示词', accent: '#9AA8BC',
    chips: [
      { n: 'prompt/persona', p: 'm', f: 'app/routines/user/mossv2/prompt/persona.py', d: '人格与交互规则片段。' },
      { n: 'prompt/protocol', p: 'm', f: 'app/routines/user/mossv2/prompt/protocol.py', d: 'CTML 协议片段：语法 / 轨道 / 边界 / 观测。' },
      { n: 'prompt/capability', p: 'm', f: 'app/routines/user/mossv2/prompt/capability.py',
        d: '能力说明：按功能分组渲染词表快照，在装配时生成。',
        w: '模型获取的能力说明由此单元输出，与执行接口使用同一数据来源。' }
    ]
  }
]

export const BAND_MAP: Record<BandId, Band> = Object.fromEntries(
  BANDS.map(b => [b.id, b])
) as Record<BandId, Band>

/* 注册名 → 证据带。用于「清单」页的分组与筛选；未命中落到 other（应为 0）。 */
const SETS: Record<string, BandId> = (() => {
  const m: Record<string, BandId> = {}
  const put = (id: BandId, names: string[]) => names.forEach(n => (m[n] = id))
  put('lang', ['__scope__', 'all', 'any', 'loop', 'code', 'shell', 'channel', 'chan_create', 'chan_delete',
    'chan_clear', 'chan_status', 'interrupt', 'notify', 'sleep', 'noop', 'wait', 'run_xml', 'code_kernel'])
  put('body', ['say', 'ask', 'show_image', 'board'])
  put('sense', ['asr', 'asr_mode', 'key_listen', 'bus_listener', 'ros_bridge'])
  put('prompt', ['sys_prompt'])
  put('tools', ['approval_gate', 'ipython', 'read_image', 'query_weather', 'screenshot', 'web_fetch',
    'web_search'])
  put('agent', ['act', 'ghost', 'mindflow', 'moss', 'mini_agent', 'xml_condenser_agent', 'send_message',
    'interrupt_agent', 'user_agent', 'world_agent'])
  put('meta', ['sysinfo', 'tree', 'tree/apply', 'tree/get', 'web_server', 'models',
    'models/delete', 'models/get', 'models/list', 'models/save', 'models/set_default'])
  return m
})()

export function bandOf(name: string): BandId | 'other' {
  if (SETS[name]) return SETS[name]
  if (name.startsWith('car/')) return 'body'
  if (name.startsWith('bb_')) return 'body'
  if (name.startsWith('prompt/')) return 'prompt'
  if (name.startsWith('routines_') || name.startsWith('routine/')) return 'meta'
  if (name.startsWith('agent')) return 'agent'
  if (name.startsWith('xml/agent')) return 'agent'
  if (name.startsWith('skill/') || name.startsWith('ssh/')) return 'tools'
  return 'other'
}

