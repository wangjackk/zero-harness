# Routine Atlas

把 zero-harness 的注册表画成一张能点的图，并且在图上走完一条经典流程：**从一个空表，到一个能说话的 agent**。

这套系统里只有一个基础单元名词 —— **routine**。hub、kernel、routine 描述连接、秩序与单元三种范围，不构成控制层级。
Routine Core 是与智能无关的通用执行与协作底座；Agent Harness 是一个 Agent 内部的模型、上下文与行动支撑结构；
Routine Society 则是人、异构 Agent、工具、服务与外部接口组成的系统级网络。三者衔接但不互相定义，也都不是第四种基础抽象。

routine 不是函数。**表里存的是类**（名字 → 类），**调用时才造实例**（id、父、状态、占用的模块），
实例构成一棵树，父停子停；`is_passive` 的那些由内核直接拉起，不接受手动创建。

它不按东西的种类组织，只按名字：一个名字能被写进标签，就能被调用，而调用方不必知道里面是什么 ——
差别只在被调用时做了什么。

注册名数量、hub 数量、调用关系数量以及 YAML / JSON / 源码出处，均是当前 zero-harness 的业务装配与文档快照，
只在拓扑和清单中作为应用实例展示，不属于 Routine Core 的固定组成；全站页脚不再用这些数字定义通用层。

## 跑起来

开发用 bun，`bun run dev` 就是 Vite dev server，改 `src/` 下的 `.vue` / `.ts` / `.css` 即时热更新（HMR），
组件状态保留，不用手动刷新；只有改 `vite.config.ts`、`index.html` 这类入口文件才会整页重载。

```bash
cd routine-atlas
bun install        # 已有 node_modules 可跳过
bun run dev        # http://localhost:5173，热更新
bun run build      # 产物在 dist/（base: './'，可直接相对路径部署）
bun run preview    # 用 dist/ 起一个本地静态服务
bun run typecheck  # 只做 vue-tsc --noEmit
```

`bun run build` 会先跑 `vue-tsc --noEmit`，类型不过不出包；`bun run dev` 不做类型检查，类型错误只在终端由 Vite 报语法级问题。

`node_modules` 是本地已装好的（由 npm 装过），`bun run dev` 会直接复用它，不必重装。
若要完全交给 bun 管理依赖，跑一次 `bun install` 会生成 `bun.lock` 并接管后续安装，
`package-lock.json` 不再需要，可以删掉。

## 三个部分，十三个页面

学习顺序为：先理解单元、模块系统与两种关系，再理解异构协作、系统拓扑、自举与能力自迭代，然后进入单个智能体与具身应用，最后查看工程实现与注册清单。主导航与命令面板均按 **Routine Core / Agent Harness / 深入系统** 分组，编号连续为 01–13。

| 路由 | 部分 | 讲什么 | 值得点的 |
| --- | --- | --- | --- |
| `/` 概览 | Routine Core | **共同世界与统一秩序**：异构主体以同一种单元参与，能力跨越边界自由组合 | 全屏 Three.js 三维星图；从单元、个体到系统的叙事；七类能力作为通用性的证据 |
| `/anatomy` 解剖 | Routine Core | 注册、声明、执行、停止与查询分别负责什么；命令实例的完整生命周期 | 通用职责说明与参考样本；九段 XMind 单元格动画展示运行、停止中与已停止 |
| `/modules` 模块系统 | Routine Core | **模块树、占用与冲突仲裁**：routine 声明所需模块，内核协调冲突执行 | 动态人体模块树与冲突锥；多 routine 依赖交集 PK；机器人 wheels 的 5/10/15 示例 |
| `/communication` 通信 | Routine Core | **系统只有通信与调用两种关系**：已有节点交换消息，调用产生临时子节点 | 四段节点网络动画；多设备 Hub 发布订阅与跨端主动调用动画 |
| `/society` 协作 | Routine Core | **异构 Routine 社会**：人、不同 Agent、工具、服务与外部接口的系统级组合 | 四段自动网络动画；平级通信、异构父子与外部适配；Harness、Agent、机器人等实现入口 |
| `/evolution` 演进 | Routine Core | **经验 · 规程 · 模型 · 混合**：不同编排形式如何共存并相互嵌套 | 四阶段自动循环；第四态展示手工、自动与智能单元的父子实例树 |
| `/topology` 拓扑 | Routine Core | **三个 hub 各带一片名字，都落进同一张表**：hub 是区域不是节点 | 点 hub 或 kernel 开抽屉、悬停看关系类型、拖动缩放 |
| `/bootstrap` 自举 | Routine Core | **从系统自举到能力自迭代**：系统建立自身，并在运行中扩展与更新能力；创作可由人或 Agent 驱动 | 八个拍点自动轮播；候选实现 → 检查与加载 → 新能力进入下次调用 |
| `/harness` Harness | Agent Harness | **Harness 的特点，Routine 如何实现**：循环、上下文、调用、许可、记忆与中断 | 六项职责映射；Flow、Pi、XML、Moss 平等呈现为具体案例 |
| `/assemble` 装配 | Agent Harness | **Agent Harness 的最小形态**：执行循环与工具单元的静态组合 | 六阶段装配动画自动循环；行动轨迹按原顺序自动回放，悬停任一条可单步复查 |
| `/robot` 机器人 | Agent Harness | **机器人 Agent 与 Routine 身体**：XML 流式行动、Moss 连续交互与编排；设备接入、运动、跟踪、导航与状态 | 两种 Agent 设计；四种 Agent 共享能力轨道动画；2 个设备接入 + 14 个 car routine |
| `/engineering` 工程实现 | 深入系统 | **当前工程实现档案**：Loader、Watcher、实时语音、三类 Agent、DAG 与 Skill | 七章八张源码架构图；11 张真实运行截图、实现来源、协议表与失败边界 |
| `/registry` 清单 | 深入系统 | 138 个注册名（py 123 · ts 9 · rust 6），一张表 | 搜名字、只看 passive、按名字/文件排序 |

两个树页不在主导航里（它们是树算法的调参与验收工具，不参与十三章编号）：

| 路由 | 讲什么 | 值得点的 |
| --- | --- | --- |
| `/life` 树的一生 | 同一份几何从种子走到散尽：破土、增粗、分枝、展叶，`stop` 一到由梢向根回收。**一片叶就是一个运行中的实例**，所以叶子少到能数（一百来片），不做体积雾、不发散光。枝干是 `create` / `start` 的父子关系，整棵子树结算完连枝一起收回——叶先落光，枝再从细枝开始一根根从父枝上断开、翻着掉到地上；主干是根实例自己的身体，没有父枝可断，最后灰掉消失，注册表不变。粗细不是照成树比例画好的：一条枝的横截面 = 自己已长出的部分 + 已长出来的子枝截面，开方就是半径，所以主干在抽枝之前只是一根细茎。叶团挂在枝梢上跟着枝尖走——芽里就揉着叶，枝往哪长、那团叶就在哪，不是等枝长到终点才凭空摆一团上去。繁茂之后还有一跳：叶实例再调一个 routine，那团叶就是它的顶芽——先鼓、再张、梢从团心穿出来、老叶转黄落掉，梢头再结一层新叶；所以同一处不会既挂着叶又长着枝。新梢也是这条枝的孩子，抽出来时宿主到主干整条路径跟着粗一点——子多撑一层，父就多长一圈 | 24 秒自动循环；`?at=0.58` 定格任一时刻，`?seed=23` 换一棵 |
| `/tree` 调参台 | 树的形态参数逐项手调，同一组参数并排长几棵；每棵树下面直接标出枝数与叶数（叶数＝实例数） | 十五个滑杆实时重算；1–4 列不同种子对比；点种子换树；「复制链接」把当前一组参数写成 URL |

## 首页主景

首页先阐述世界观与组合优势，不承担注册机制、语言实现或实例状态的教学。主线是：复杂世界不必形成割裂系统；
不同主体保留差异，却能以同一种单元参与共同秩序。hub、kernel、routine 分别表达连接、秩序与单元三种范围，
不构成控制层级；它们共同带来异构共存、能力流动、实现替换与自由重组。Core、Harness、Society 再从单元、
个体和系统三个尺度展开同一世界观，具体技术细节由后续章节承担。

`RoutineField.vue` 与 `lib/routineScene.ts` 使用 Three.js 构建全屏三维宇宙循环。开场为奇点，
随后尘埃爆散并聚合成恒星、行星与卫星；恒星系形成后，镜头持续拉远，中央星系缩小，远处星系群逐层显现。
最后全部结构旋转坍缩回奇点，约 44 秒后开始下一轮。稳定阶段仍有小星体与卫星错相生成、运行和消散。
场景不显示注册名；三维表面、星尘、轨道与辉光仅承担视觉表达，不增加额外机制含义。指针只改变观察视差，
不改变调用关系。画布固定在导航栏以下的整个视口，作为整个首页的持续背景；上下滚动只移动前景章节，
首屏离开视口不会暂停动画。全页只使用同一层固定、均匀的轻度遮罩维持文字对比度；首屏与后续章节不设独立底色或渐隐，章节交界不会出现明暗断层。动效自动循环，不设播放控件；
减少动态效果模式直接显示完整静态构图，WebGL 不可用时降级为静态 CSS 构图。

## 模块系统：声明、冲突与仲裁

`/modules` 属于 Routine Core，位于解剖之后。模块是被 routine 占用的资源节点，不是另一种 routine；
模块树描述占用关系，命令实例树描述调用与生命周期，二者不能混为一谈。

`Routine.modules()` 以“类型 + 入参”的纯函数声明占用，created 回执把声明交给内核与父调用方。
`start` 尝试占用声明模块，运行中可用 `acquire()` / `release()` 申请与释放，停止时由内核全量回收。
每个模块的冲突锥包含祖先、自己与后代；第三方占用落在冲突锥内即发生冲突，自己或祖先的占用允许父子共占。

`priority` 只在冲突时仲裁，不做全局任务排序或隐式排队：无冲突直接启动；严格高于全部冲突持有者才可驱逐；
平级或更低则启动或申请失败。多模块声明按整组冲突锥找出全部冲突持有者，不能拆成互不影响的单模块裁决。
裁决动画用两个 routine 的依赖集合相交：Routine 1 正在运行并以优先级 10 占用 A、B、C，Routine 2 尝试请求 C、D。
请求光点沿 Routine 2 的两条依赖线流动，只有到达交集模块 C 时触发 PK，D 无冲突，A、B 也不参与比较。
Routine 2 为 15 时，Routine 1 停止并释放 A、B、C，Routine 2 整组启动并占用 C、D；为 10 时整组收到 StartError，D 也不会被单独占用。
运行中的 routine 使用循环旋转描边，有效依赖线上持续流动光纹；停止或启动失败后对应动态效果同步停止。
动画离屏暂停，减少动态效果时显示首轮结果。
`ModuleTreeDemo.vue` 用四段自动动画演示人体模块树：先展示 `body → arm → hand` 的父子结构，再占用 `right_arm`，沿树边展开包含祖先 `body`、自身和后代 `right_hand` 的冲突锥，最后点亮可并行的左侧分支。
该示例不冒充系统固定结构；占用右臂会与右手冲突，左臂与右臂可以并行。
该示例不冒充系统固定结构。随后以真实机器人 `wheels` 为例，列出 `car/gaze` 5、普通动作 10、
`car/wake_track` 15；这些档位是应用选择，不是 Core 固定的等级制度。

## actor 之间如何通信

`/communication` 用抽象节点网络只说明通信与调用，不展开具体方法或工具。`listener` 与 `agent`
都是已有且持续运行的 routine；二者之间用一条双向虚线表示通信，消息可以双向流动，但不创建节点。
agent 与另一个 routine 之间用一条单向实线表示调用，调用生长临时子节点并建立父子生命周期。
四段动画只展示在线、通信、调用、结束，不另拆输入、回应或返回阶段。临时节点结束后，
已有节点与通信关系继续存在。

动画不提供播放或阶段控件，进入视口后按在线、通信、调用、结束自动循环；离屏停帧，减少动态效果时显示静态完成态。窄屏图内横向浏览。

`DeviceInterconnect.vue` 补充多类型设备接入：Android 手机、机器人和桌面电脑各自运行 RoutineHub，经 gRPC 拨入同一 kernel，
注册 `phone/*`、`car/*`、`pi/*` 等能力。hub 不是只接收调用的被动工具端点：其上的 routine 可以发布与订阅消息，
也可以按注册名主动调用其他 hub 的 routine。动画以手机 `phone/sensors/watch` 持续发布 `phone/sensors`、
桌面 hub 订阅接收，随后桌面 hub 主动 `call("car/move")` 调用机器人并接收结果，展示这两条主动路径。
设备不是单个 routine，而是由设备上的 hub 提供一组本机 routine；实现语言可替换，不作为系统接入的约束。
`routine-kt/phone-hub` 通过 `RoutineHub.connect(Kernel.remote(address))` 拨入其他进程或设备上的 kernel，手机侧不持有 kernel；
Routine API 已提供 `create/call/publish/subscribe`，`PhoneSensorsWatch` 是真实感知上报实现。
此动画说明通用接入与主动协作机制；拓扑页和注册清单仍描述 zero-harness 当前静态配置。

## 协作：异构 Routine 社会

`/society` 讨论系统级组合，而不是某个 Agent 的内部结构。人、不同 Harness 的 Agent、工具、服务和规程都可以参与；
统一的是注册、通信、调用与生命周期，不是智能、类型或内部框架。多 Agent 只是异构网络的一种情况。

平级通信连接已有实例，不建立父子关系；调用可以生成与父节点不同类型的子 Agent、人工规程或工具，父子关系来自调用而非类型。
外部 Agent 提供接口时，可由适配 routine 转换协议、任务与结果；父停止保证回收内部适配器子树，但外部任务是否取消取决于外部接口，
不能将接入写成对外部生命周期的自动控制。具体 Harness 是否向模型开放创建、通信或子 Agent 入口，仍由实现与白名单决定。

同一套注册、通信、调用与生命周期机制，可以承载 Agent Harness、异构 Agent、机器人与开放系统，页面以这些真实实现说明 Routine 的组合优势。
四段动画依次展示异构共存、平级通信、Flow→Pi 异构子 Agent 调用和外部 Agent 接口接入，无播放控件，离屏停帧。

## Harness：特点与 Routine 实现

`/harness` 首先对应六项职责：Agent 自身作为 routine 承载执行循环；Harness 组织模型与上下文；能力经注册和调用进入执行；
权限许可也是 routine，Harness 等待许可结果后决定是否调用目标；EventLog、SQLite 或独立 routine 承载记忆与恢复；
中断传入执行侧，并由 stop 与父子级联停止回收实例。不是每个 Harness 内部对象都要成为 routine。
六项不强求统一图式：执行循环用闭环，模型上下文用多路汇聚，能力调用用去程/回程双路径，权限用允许/拒绝闸门分支，记忆用日志堆叠与读写，中断用父子实例树级联。路径流动表达方向，离屏暂停，reduced-motion 静态呈现。

随后用仓库中的四套实现作为具体案例。它们共享 Routine Core 的注册、创建、通信、停止与回收，
不共享同一个 Agent 循环。页面按真实代码给出创建与调用链、内部组件、行动协议和状态真相源：

- **Flow**（`agents/flow`）：项目内 `FlowLoop` 掌握 turn/step；`Inbox` 接收输入，`EventLog` 按 session 保存 JSONL 事件，
  `ToolScheduler` 将 function call 逐个经 `create → start → wait` 派发给 `meta.tool` routine；各调用立即并发，不按模块冲突编排。FlowHub 是 HTTP/WS 前门，
  `flow_manager` 负责创建与管理 `flow_agent`。代码已实现，但当前 Python 主清单未启用该包。
- **Pi**（TypeScript `agents/pi`）：`pi/agent` 包装 SDK `AgentSession`，会话主循环、压缩与重试由 SDK 托管；
  模型只看到 `ipython` custom tool，宿主用 `create/start/wait` 调用 Python 侧 routine，并用 AbortSignal 传播停止。
  SDK 管会话消息，共享 SQLite 只保存 Agent 注册元数据。当前 TS 增量清单启用。
- **XML**（Python `xml/agent`）：`XmlAgent` 在 epoch 内反复 react，每轮创建一个 `act` 子 routine，
  将 XML 流解析为 body Shell 的工具调用；有效 feedback 驱动下一轮。本地 SQLite Memory 保存消息、结果与摘要，
  Reflex 决定在途输入的处理方式。当前采用 Python 版，TS XML 已下线。
- **Moss**（`mossv2`）：独立 `moss.yaml` 与固定身体装配；Mindflow 仲裁注意力，Ghost 执行一拍，
  CTML 经 Shell 与 Channel 进入动作 lane。Moments 组织关键帧；普通结果只入史，next、owed echo 或迟到 beat 才驱动续拍。
  它不是 manager 模板，也不是 turn/step 工具循环。

四种实现只作为并列案例呈现，不再单独展开 Flow 的执行动画，也不把某一种 Harness 的循环套用到其他实现。

## 工程实现与运行实录

`/engineering` 介绍 zero-harness 的当前工程实现，以源码架构和真实运行界面说明具体设计。
页面分为 Loader / Watcher、实时语音、Pi 单工具 Agent、XML Agent、Moss、DAG 和 Skill 七章，提供粘性目录、滚动高亮与锚点深链。
`EngineeringDiagram.vue` 提供八张源码架构图，区分外部进程、Agent、Routine 执行面、持久化、控制流与反馈流；移动端可横向查看完整图形。
版式独立于其他图集页面：固定浅色文档令牌、单栏正文、架构图、组件职责表、调用序列代码块、实现路径、边界注释、待确认事项表和上下页导航；
移动端目录折叠，工程页不使用全站通用页脚。XML 和 Moss 两章明确定位为机器人交互；XML 接入动作回执与连续 ASR 会话截图，Moss 接入 CTML／observe 输出和 Moments 帧链截图。
Loader / Watcher 章已接入两张 Pi Agent Creator 真实运行截图：用户提出“现场写一个新能力”，Pi 通过 ipython 创建 `dynamic/deep_research.py`，Watcher 热注册后完成两轮运行并给出 `run_routine` 调用方式；不再沿用机器人运动编排的拍摄剧本。
Pi 复用同会话的 ipython / run_routine 证据；DAG 使用 approval_dag 完成态；Skill 接入共享库摘要／全文选择与 routine-bridge 正文详情；独立语音接入 Bus Monitor 的 wake／VAD／ASR／orch 真实事件。七章共 11 张运行截图，均可打开完整原图。

`EngineeringMedia.vue` 展示正式图片或视频；视频不自动播放且采用懒加载，图片可通过 `href` 打开未裁剪的完整截图。
语音章节按 `sherpa-onnx/services` 源码补充 ZeroMQ 总线、mic/KWS/VAD/ASR/orch、AEC 与 playback 的听觉架构，并与 zero-rs speak / TtsService / AudioService 的说话链分栏呈现；两者是独立实现，不表示 Moss 的当前接线。
当前 ASR、VAD 与 local KWS 均订阅 `voice.audio.raw`，不能写成识别已使用 AEC clean 流；playback-rs 与 zero-rs AudioService 也不默认互通。
Moss 另有自有语音面：`mossv2/asr` 直接采集并连接豆包 SAUC，`commands/say.py` 使用公共 tts 包的 SeedTTS 与 LocalSpeaker；其设计参考并适配原版 `MOSShell`，文档明确标注来源。Moss 默认 `mute_on_say=true`，听说为半双工；DAG 取消与热更新中的既有实例也在对应章节说明其实现边界。

## Agent Harness 的最小形态

`/assemble` 明确属于 Agent Harness 层：Routine Core 不依赖模型，Harness 在其上组织模型、上下文与行动循环。
本页用一个执行循环，加上 `Read`、`Grep`、`Write` 和 `Bash` 展示最小应用，不给 routine 增加特殊类型。
开场先把 agent 与四个 tool 画成同形、同尺度、无连线的平级单元；随后同一批单元移动到装配位置，
agent 不扩大为容器。连线表示调用关系，不表示包含或类型层级。注册态、同名多实例与停止传播属于
生命周期主题，不在此页展开。

1. **平级单元** —— agent 与 tool 具有相同的声明和调用形式，位置不构成层级。
2. **一个执行循环** —— 组织上下文、请求模型、调用模型指定的单元，并回填结果。
3. **接入读取与检索** —— `Read` 提供文件读取，`Grep` 提供内容检索。
4. **接入写入与执行** —— `Write` 提供文件写入，`Bash` 提供命令执行。
5. **形成最小 agent** —— 工具结果返回执行循环，进入下一轮上下文，直至不再产生调用。
6. **装配产物仍可被调用** —— 上层调用 `agent`，与 `agent` 调用工具采用相同的调用形式。

常驻细线表示可调用关系，移动短划表示调用及结果返回。六段按序自动循环，阶段条只作进度指示；
不设播放、暂停或阶段切换控件。窄屏使用图内横向浏览，不压缩节点文字。

装配图下方的行动轨迹以轻量记录样本说明可观察性：每一步对应一次 routine 调用，选择记录可查看调用者、
输入、返回与耗时；记录按原顺序自动回放，悬停任一条即可单步复查，不重新执行工具，也不产生副作用。样本不是实时运行数据。

## 机器人：Agent、Routine 身体与异构协作

`/robot` 不展开完整运动架构。Agent 本身介绍两种具身实现：XML 将模型 XML 字流送入 Act，行动随输出发生，
Reflex 处理在途输入；Moss 由 Ghost、Shell、Channel 持续消费字流，以 CTML、lane 和 all / any / loop 编排行动，
Mindflow 接收新感知并决定继续、打断或续拍。二者都面向流式、可打断的连续交互，不把输入输出锁成固定问答轮次；Moss 当前自有语音面默认说话期丢麦，属于听说半双工。

Routine 身体依据 `car`、`hiwonder` 与 `device_bus` 的当前 Python 实现列出：`ros_bridge`、`bus_listener`
两个设备接入 routine，以及运动、跟踪、导航、地图、状态和相机等十四个 `car/*` routine。
软硬结合只沿输入与输出说明：Agent 调用能力 routine；运动与导航实现经 rosbridge WebSocket 向
`/controller/cmd_vel`、`/nav_goal` 发送消息，拍照请求经设备总线发往车头相机；电池、里程计、AMCL、
语音和视觉数据经桥接 routine 回到软件系统，由相应 routine 读取并产生结果。

机器人只通过 `wheels` 使用 Routine Core 的模块系统；通用模块树、冲突检测和优先级规则已移至 `/modules`，
本页仅保留具身应用说明与入口，不展开完整运动仲裁架构。

XML、Moss 与 Pi、Flow 保留各自 Harness，同时处于同一 Routine 社会：以 routine 形式被发现、通信和调用；
平级通信用于协作，调用形成异构父子实例。星系动画中，Pi、Flow、XML、Moss 四颗恒星同时存在，
同一组运动、跟踪、导航、状态、相机和设备桥接行星在不同 Agent 之间迁移，表达四种 Agent 共享调用、
各自组合同一批底层 routine。页面文案只说明共享关系，不展开动画过程。

## 社会系统中的编排如何演进

`/evolution` 讨论编排由谁承担、以什么形式存在，而不是普通执行过程如何变成 routine。
人、文本规程、工具和 agent 都可抽象为单元，调用语义保持一致。经验、文本与模型不是相互淘汰的线性阶段，
而是可以共存并相互嵌套的编排形式。

| 阶段 | 编排承担者或形式 | 主要性质 |
| --- | --- | --- |
| 经验编排 | person · 人脑 | 人依据经验与当下判断组织调用，编排尚未获得独立文本形式 |
| 规程编排 | sop · 文本 | 顺序、分支与条件被写成明确规则，执行者依照规程调用其他单元 |
| 模型编排 | agent · model | 模型依据目标与上下文生成后续调用；agent 与其他单元没有抽象特权之分 |
| 混合编排 | 手工 · 自动 · 智能 | 人、规程与 agent 可以互为调用环节，不同实现类型在同一棵父子实例树中组合 |

前三态保留同一组外围单元和中央位置，分别观察编排承担者与调用路径的生成方式。
第四态同时展示 `person → agent → person` 与 `person → sop → 单元`：人调用 agent，
agent 在确认环节再调用人，自动规程也在同一棵树中执行。父子关系属于命令实例，父停子停。

第四态下方只保留一个通用关系：一个 routine 只要在执行中调用另一个 routine，就在这次调用中承担编排；
它同时也可以被上层调用。编排器是关系位置，不是特殊实现类型或能力等级。
person、sop、agent、DAG、行为树、状态机均作为同一命题下的等例，不再把后三者单独分类。
agent 的差别仅在于由模型决定后续调用；DAG、行为树与状态机则采用不同的内部结构。

`DagRoutine.vue` 以 `zero-py/app/routines/user/dag` 的真实实现展示这一组合能力。
外部调用 `run_dag`，内部执行 `hello_dag`：`fetch_a` 与 `fetch_b` 两个 `query_weather` 实例并发，
完成后依次解锁 `merge` 与 `summary`，最后将 `summary` 聚合为整体 `result`。
`nested_dag.yaml` 的节点再次调用 `run_dag`，说明子 DAG 对父级仍是普通节点，无需新增系统类型。
动画分为调用、并发、汇合、传播、返回五段，循环播放，不设播放控件；离屏停帧，减少动态效果时停在返回段。

`StructureRoutine.vue` 另以两段轻量动画说明同一组合原则：行为树由选择节点先调用条件 routine、再调用动作 routine；
状态机由事件驱动迁移，再由当前状态调用动作 routine，最后都把内部结果返回给上层。
二者明确标为“结构示意 · 非现有实现”，不把概念支持写成已有代码。两段动画同样循环播放、不设播放控件，离屏停帧，减少动态效果时停在返回段。

本章不展开注册与实例化细节（属于「解剖」），也不讨论系统修改自身能力（属于「自举」）。

主线动画四态循环播放，不设播放控件与阶段切换；减少动态效果时直接呈现第四态的完整构图。
阶段条只标示当前处于哪一态，不可点击。

## 拓扑怎么摆：三个 hub 各带一片名字，都落进同一张表

这一页改过三次，前两次都错在同一个地方：**把 hub 画成了节点**。

画成节点，它就和那些点并排站着 —— 读者看到的自然是「一堆 routine，旁边站着几个 hub」，
两者像是互不相干。而事实是 `pi/agent` 之所以在表里，**是因为 ts 那个进程把它带进来的**。
这是包含关系，不是相邻关系。

```
        ┌─ hub · py ─ 同进程 ─────────────────────┐
        │  ① 元能力  ② Agent  ③ 编排  ④ 工具     │
        │  ⑤ 身体    ⑥ 感知    ⑦ 提示词           │
        └──────────────────┬──────────────────────┘
                           │
   ┌─ hub · ts ──┐   ┌─────┴─────┐   ┌── hub · rust ─┐
   │  ② Agent    │───┤  kernel   ├───│   ⑤ 声音      │
   │  pi/agent … │   │ 名字→实例  │   │  speak ……     │
   └─────────────┘   └───────────┘   └───────────────┘
```

三条规矩：

1. **hub 只有三个：py、ts、rust。判据是「它往表里放了名字」。**
   go 有完整的 SDK（`routine-go`：context / hub / lifecycle / grpc），但一个注册名都没放 ——
   没有名字就没有位置可画。device 是「任何带 IP 的东西」，它拨进来之后用的也是上面某个 hub 的协议，
   当前没有一条 `device/*` 的注册名，所以同样不画。
2. **hub 是一片区域，不是一枚标签。** 它包含自己带进来的那些点；标签（`hub · py`）贴在
   区域底边的正中、压着那条边，就是 fieldset legend 的做法 —— 只有贴在直边上，框线才会
   被这块标签真正切断。标签若落在圆角上或整个浮在框外，读起来都是「有个标签掉在旁边」。
   py 那片最大（四十来处）不是排版好看 —— 是因为它跟 kernel 同进程、带得最多；
   ts / rs 各三处。**大小是事实。**
3. **表只有三个入口，入口全是 hub。** 三条线各自从自己那片区的边上出去，一条线同时表示
   「名字注册上去」和「帧送回来」—— hub 不实现能力、也不持有实例，所以没有第二条关系要画。
   区里面那些名词（包括装载器 `routines_loader`）**一根线都不接到表上**：它们在自己那片区的
   边界之内，拉起来的名字走的仍然是这个区唯一的出口。画了就等于说「表还有第四个入口」。

颜色分两套，别混：**云的颜色 = 谁带进来的**（py 青绿 / ts 紫蓝 / rust 橙），
**点的颜色 = 哪一类能力**（七条证据带）。所以同一类能力会出现在两片云里 ——
② Agent 在 py 云和 ts 云里都有，⑤ 身体在 py 云和 rs 云里都有。
那不是重复，那是这张表的真实样子：**同一类能力被两个 hub 分担。**

点上写的就是**routine 的名字本身**（`routine/list`、`ipython` —— 能直接敲进标签的那种），
`×N` 是折叠角标：一个点装 N 个同前缀的名字（`chan/*×5`）。早先有一块写的是「控制原语」——
那是个归类，不是表里的键，已经拆成 `interrupt` / `notify` / `sleep` / `noop` 四个点。
标签排不下的会先收起来，指到那个点上才浮出来（同时把邻居压暗，否则它出来也是叠着的）。

区里唯一标成橙色虚线的是**装载链**（`routines_watcher → routines_loader`）：
它讲的是代码变化如何重新进入注册表，跟"名字首次进表"是两件事，所以它整条留在 py 区内、不伸出来。

`agent_hub` 是个反例里最有用的一个：它本身是个注册名（能被 `routine/list` 到，所以它在 ② 带里），
干的是 hub 的活。图上多一圈虚线，并且 `hub · ts` 有一条 `register` 连到它 ——
pi 的 manager 注册到的是 py 侧那个 `agent_hub`，这条线必须跨区，因为**跨语言**这件事只有跨区才看得出来。

## 数字从哪来

名字是全语言**同一张表**，出处分两半（`src/data/names.ts` 合成）：

- **123** 个由 py 注册 —— 探针从真实 loader 导出（`src/data/registry.json`），不是数的 yaml 行
- **9** 个由 ts 的 hub 拨进来（`pi/agent`、`pi/agent/manager`、五个 entry、`pi/settings/*`）
- **6** 个由 rust 的 hub 拨进来（`speak`、`audio/output/*`、`music/*`、`sound_effect`）

合计 **138** 个 routine（也就是 138 个注册名，这两个词说的是同一件事）、**7** 条证据带、
**38** 条关系、**3** 个 hub + **1** 个内核。

图上只画 **54 个点**：一个点 = 一个名字，或一族同前缀的名字（`chan/*` 的 5 个收成一点，
角标写 `×5`）。**这个 54 是画法，不是系统事实** —— 它不该出现在页面上当指标，
真正要说的是「有多少个 routine」，那个数是 138。

ts / rust 那几个是手抄的，但每一个都能在代码里指出注册名（ts 是 `static name = '...'`，
rust 是 `const NAME: &'static str = "..."`）。共享 kernel 里名字**全局唯一** ——
ts 侧只带 py 侧没有的 pi 引擎包，重名会被 kernel 拒绝注册，所以它们落进 ts 那片云里，
点上还多一圈细环（不是 py 注册的）。

图上的边只画"能从代码里指出来"的关系（注册、调用、`req`、同族归并、注入、自举），
每条边上写着它到底是被哪一句代码牵起来的。`kernel` 和三个 `hub` 不是 routine，
出现在图上是因为它们是被指的对象 —— 抽屉里写了为什么它们不算。

## 目录

```
src/data/     事实层：registry.json（py 探针导出）、names（全语言合成）、bands、edges、criteria、mechanisms、assembly（表）、bootstrap
src/pages/    七个页面
src/components/  头部、页脚、命令面板、抽屉、星空、数字滚动、两张舞台（Assembly / Evolution）
src/lib/      动效工具（缓动 / 贝塞尔 / 路径取点）、高亮、主题、reveal 指令
src/styles/   设计令牌 + 全局版式
public/       favicon（七个点绕一圈，和 logo 同一套坐标）
preview/      整屏截图
```

`src/data` 是唯一事实来源，页面里没有硬编码的计数 —— 加一个注册名，七个页面的数字一起变。

## 几个做法

**不用图表库，也不用动画库。** 拓扑是确定性布局：py 那片在上、ts 与 rs 在下两角、kernel 落在三者正中，
三条注册线因此全是直线（一条竖直落下来、两条水平插进去）；py 区内七个带手排成 4+3
（手排的落点，不是公式 —— agent 带 13 块、prompt 带 3 块，云团半径差一倍，等角度分必然叠在一起）。
带内按黄金角向日葵螺旋铺开。装配页的表用固定种子的抖动铺点（同一个下标永远同一个偏移，
刷新不跳），所以它看起来是一片“各干各的”，而不是一张排好的网。
砖的位置、射线、镜头缩放，全是 CSS transition / keyframes + 一个游标定时器 ——
没有 rAF 循环，也没有可拖的时间轴：**这一页的状态只有一个「现在第几拍」**。

**没有播放控件。** 页面上的动画都进入视口即按固定节奏自动循环，不提供暂停、重播或手动步进；
阶段条只标示当前位于哪一段，不接受点击。组件离屏停帧，`prefers-reduced-motion` 直接落终态。

**标签不打架。** 按 y 贪心排序，每个节点试四个候选偏移，四个都撞就隐藏，悬停时再出来。
文字和药丸都用 `paint-order: stroke` 先描底色再填字，边从底下穿过也不会看不清。

**代码高亮是单趟分词。** 一条正则交替分组，一遍扫完拼 html。链式 `replace` 会把上次
注入的 `<i class="n">` 再匹配一次，`class` 会被当关键字染色。

**深色是本体。** 默认深色，浅色是同一套令牌的翻面；系统偏好只在手动切过一次之后才生效。
字体自托管（Inter / JetBrains Mono），没有 CDN 依赖。

**动效只做一次。** `v-reveal` 用 IntersectionObserver 进场抬起，`prefers-reduced-motion`
直接落终态 —— 所以截图脚本用 `reducedMotion: 'reduce'` 就能拿到稳定画面。

## 边界

数据是 2026-10 的一次快照。系统还在长，`src/data` 里的三份清单（`registry.json`、`bands.ts`、
`edges.ts`）要跟着 loader 重新对齐；页面本身不含任何会过期的推断，只显示这三份清单里写下的东西。
