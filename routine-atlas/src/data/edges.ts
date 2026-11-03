/* 真实关系边。只画能在代码里指出来的：投递、call、req、器官与门、注入、自举、投递场。
   目标必须存在于 bands.ts 的点里，或下面四个 ghost（非 routine 的端点）。

   ghost 这一层是**结构**，不是标签：一个 kernel + 三个 hub。
   README 的原话是「kernel 内嵌于 Python 主进程，外部 hub（Rust / TypeScript）经 grpc dial-in 拨入」。

   **hub 只有三个，判据是「它往表里放了名字」**：py、ts、rs。
   - go 有 SDK（routine-go：context / hub / lifecycle / grpc），没往表里放一个名字 ——
     它证明的是“任意语言都能写 routine”，但没有名字就没有位置可画。
   - device 是“任何带 IP 的东西”，它拨进来之后用的也是上面某个 hub 的协议；
     当前没有一条 `device/*` 的注册名，所以同样不单独画。
   画出来的三个各自**拥有**一批点（`owns`），图上它们不是跟点并列的节点，
   而是包住自己那批点的区域 —— 名字经 hub 注册进表，这是包含关系，不是相邻关系。 */

export type EdgeKind = 'hub' | 'call' | 'req' | 'org' | 'inject' | 'boot'

export interface Edge {
  f: string
  t: string
  k: EdgeKind
  label?: string
}

export const KIND: Record<EdgeKind, { color: string; dash: string; label: string; note: string }> = {
  hub: { color: '#8A94A6', dash: '', label: '投递 · 注册', note: 'hub 按注册名发起调用，不实现能力，也不持有命令实例。该通道同时用于注册名登记和帧返回；每个实现区域使用同一入口完成收发' },
  call: { color: '#5A6474', dash: '', label: 'call', note: '父子命令实例：调用方等待子实例，子实例生命周期附属于调用方' },
  req: { color: '#3ED2E0', dash: '5 4', label: 'req', note: 'p2p 事件请求：等待回执或超时' },
  org: { color: '#46D8A0', dash: '', label: '对象 ↔ 接口', note: '底层对象不进入词表，单元提供其调用接口' },
  inject: { color: '#A98BFA', dash: '1.5 4', label: '注入 prompt', note: '三个片段组成 system prompt' },
  boot: { color: '#F0B24A', dash: '6 4', label: '自举链', note: '重载与代码更新链。注册不经过此链，注册名仅通过 hub 写入注册表' }
}

/* 谁算一个 hub：能拨进 kernel、能往表里放名字的那个进程。
   py 与 kernel 同进程，其余经 grpc dial-in —— 这条差别写进 sub，因为它是实现的事实。
   `owns` 是这个 hub 实际往表里放的名字属于哪种实现；图上它按这个把点圈进自己的区域。
   `accent` 是那片区域的底色 —— 三个 hub 三种颜色，跟七个证据带的色系刻意错开，
   否则读者会把“哪片是哪个 hub”和“哪团是哪个带”看成一回事。 */
export interface Ghost {
  title: string
  sub: string
  w: number
  note: string
  owns?: 'py' | 'ts' | 'rs'
  accent?: string
}

export const GHOSTS: Record<string, Ghost> = {
  '@kernel': {
    title: 'kernel',
    sub: '注册名 → 提供方',
    w: 132,
    note: 'kernel 不是单元，而是注册表及路由机制。它不实现具体能力，仅解析注册名的提供方及命令实例的归属。内嵌于 py 主进程（routine-py 自带 asyncio 实现，无需单独部署），并通过 grpc 连接其他 hub。'
  },
  '@hub-py': {
    title: 'hub · py',
    sub: '同进程',
    w: 104,
    owns: 'py',
    accent: '#2FC2B0',
    note: 'py 是 123 个注册项的入口，覆盖元能力、agent、编排、工具、设备、感知和提示词。其与 kernel 位于同一进程，无需 grpc；图中区域较大反映注册项数量。'
  },
  '@hub-ts': {
    title: 'hub · ts',
    sub: 'dial-in · pi',
    w: 112,
    owns: 'ts',
    accent: '#7A8CF0',
    note: 'TypeScript 独立进程通过 grpc 连接同一 kernel（127.0.0.1:7795），仅注册 py 侧未提供的 pi 引擎包。共享 kernel 要求注册名全局唯一；其 9 个注册项在图中折叠为 3 个节点，属于 Agent 类别。'
  },
  '@hub-rs': {
    title: 'hub · rust',
    sub: 'dial-in · 音频',
    w: 118,
    owns: 'rs',
    accent: '#E08A3C',
    note: 'Rust 独立进程连接同一 kernel，注册 audio/output/*、music/* 和 speak。实时音频由 Rust 实现以满足稳定性要求；6 个注册项在图中折叠为 3 个节点，属于动作与表达类别。'
  }
}

export const EDGES: Edge[] = [
  /* ---------------------------- 入口 → 表 ----------------------------
     三条都进同一个 kernel。结构上它们完全平级 —— 没有“主 hub”；
     py 那个区更大只是因为它带的名字多，不是因为它更“高”。 */
  { f: '@hub-py', t: '@kernel', k: 'hub', label: '同进程' },
  { f: '@hub-ts', t: '@kernel', k: 'hub', label: 'grpc' },
  { f: '@hub-rs', t: '@kernel', k: 'hub', label: 'grpc' },

  /* agent 是另一种 hub：它登记的是“有哪几种 agent 实现”。
     ts 的 pi manager 注册到的是 py 侧的 agent_hub —— 这条线要跨出自己那个区，
     因为“跨语言”这件事只有跨区才看得出来。py 自己那条不画：
     它和 agent_hub 在同一个区里，一个区只留一个出口。 */
  { f: '@hub-ts', t: 'agent_hub', k: 'hub', label: 'register' },

  /* ---------------------------- 自举 ----------------------------
     装载链只画到装载器为止，**不画一根插进表的线**。loader 就在 py 那片区的
     边界之内：它拉起的名字，走的仍是那个区唯一的出口。再画一根 loader → kernel，
     图上就等于说“表还有第二个入口”，那三条主干也就不再说明任何事情。 */
  { f: 'routines_watcher', t: 'routines_loader', k: 'boot', label: '重载' },

  /* ---------------------------- 装配 ---------------------------- */
  { f: 'moss', t: 'mindflow', k: 'call' },
  { f: 'moss', t: 'ghost', k: 'call' },
  { f: 'moss', t: 'asr', k: 'call' },
  { f: 'moss', t: 'board', k: 'call' },
  { f: 'moss', t: 'say', k: 'call' },
  { f: 'ghost', t: 'mindflow', k: 'call', label: '节拍分配' },
  { f: 'mindflow', t: 'xml/agent', k: 'call', label: 'owed 触发' },

  { f: 'shell', t: 'all', k: 'call' },
  { f: 'shell', t: 'any', k: 'call' },
  { f: 'shell', t: 'loop', k: 'call' },
  { f: 'shell', t: 'code', k: 'call' },
  { f: 'shell', t: 'chan/*', k: 'call' },
  { f: 'code', t: 'ipython', k: 'call', label: 'CodeKernel' },

  /* ---------------------------- 调用 ---------------------------- */
  { f: 'xml/agent', t: 'ipython', k: 'call' },
  { f: 'xml/agent', t: 'read_image', k: 'call' },
  { f: 'xml/agent', t: 'car/*', k: 'call' },
  { f: 'xml/agent', t: 'ask', k: 'call' },
  { f: 'xml/agent', t: 'web_*', k: 'call' },
  { f: 'xml/agent', t: 'skill/*', k: 'call' },
  { f: 'xml/agent', t: 'mini_agent', k: 'call', label: 'spawn' },
  { f: 'xml/agent', t: 'xml_condenser_agent', k: 'call', label: '压缩' },
  { f: 'approval_gate', t: 'ipython', k: 'call', label: '调用前审批' },

  /* 跨语言那一跳到这张表里看不出区别，所以单独标一句：pi 的唯一工具是 ipython */
  { f: 'pi/agent', t: 'ipython', k: 'call', label: '唯一工具' },

  { f: 'car/*', t: 'bus_listener', k: 'req', label: 'req snap' },
  { f: 'car/*', t: 'ros_bridge', k: 'req', label: '话题' },
  { f: 'board', t: 'bb/*', k: 'org' },

  { f: 'asr', t: 'mindflow', k: 'req', label: 'declare' },
  { f: 'key_listen', t: 'asr_mode', k: 'call', label: 'ptt' },
  { f: 'asr_mode', t: 'asr', k: 'call', label: '麦克风模式' },

  { f: 'prompt/persona', t: 'xml/agent', k: 'inject' },
  { f: 'prompt/protocol', t: 'xml/agent', k: 'inject' },
  { f: 'prompt/capability', t: 'xml/agent', k: 'inject' }
]
