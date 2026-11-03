# mossv2 决策台账

**这是依据簿, 不是路线图** —— 路线在 `ROADMAP.md`(往哪走 / 走到哪了).

这里回答的是另一类问题: 每条决定**为什么**是它(含被推翻的前一版), 以及落地
时踩出来的细节. 写法故意保留「决定 + 依据 + 演进」: 下一轮要推翻它的人需要
看到当时看到了什么 —— 只留结论, 同一个坑会被重新踩一遍.

分工: 机制怎么跑在 `docs/orchestration.md`, 代码里的「为什么这么写」在各模块
docstring, 选择与推翻的来历在这里.

---

## 架构(2026-10 形态, 详表)

- **Ghost 灵魂**: 记忆(**有界滑窗 Memory, 出窗即忘** —— 脑子每拍看到的与 recall 读到的是同一份窗口视图) + 思考 + 外接脑子(`brain_factory` 类属性注入, 可换). 不解字面, 不执行动作. 入口 `think`: 一拍 = (可选)外界输入入记忆 → 脑子产字流逐块转身体 → 观测回灌记忆. 何时想/想几拍归桥梁.
- **Mindflow 桥梁**: 裁判循环(原版 `_on_impulse_consuming_loop` 同构: 信号即醒 + 0.5s 空搏动**保活**[原版超时裸 continue 不打 rank] —— 重赛由事件驱动: 新 declare 点灯或链终点灯[原版空缺期 thinking 循环 1s 扫描的替身]; idle 判定留空搏动[原版在 thinking 循环, mossv2 无此循环]) + 检录缓冲+信号线(原版 nuclei buffer + `_has_impulse_event` 同构: declare 只登记点亮, 拉取权 100% 在裁判手里) + **多源仲裁拉取时打**(原版 rank 预选赛 + challenge 决赛两场结构: rank 群比选头号挑战者[同源聚合一拍/特殊核 last-wins 独占/尾包快车道/字典序先 priority 后 strength] → 挑战现任 Attention —— 保护期/衰减/同源提权/吸收; **单源成链**: 一拍由一个源驱动) + 失败处置(default → suppress 回检录缓冲重赛[下届独立成链]/notify·BACKGROUND → 挂账/next → 挂账+插队/knock·command → 丢/strength=0 → yielded 绝不竞争) + **关注点灯(一帧一戳 owed)**(`_request_beat` 一盏闩锁三个点灯人: next 输家 queued / 灵魂报本拍有 owed 的 echo / 灵魂敲 beat[债晚到]; **错误自带债, 成功靠 `observe="true"` 认领** —— 未声明的成功只记账不驱动, 入史下一届缝合缝读到) + **中断**(一拍抛错 = 收链: 泵不崩, reason 留记忆) + **declare 申报登记**(declare ≈ 原版外界信号入口 add_signal/on_signal —— signal.__state__ dispatched/ignored ↔ verdict 同构; fire-and-forget: 只回 accepted/dropped/noop/ignored 注册级回执, 仲裁结果调用方不可见) + **idle 静默屏障**(无在途链 + 检录缓冲空 + 无挂账; 测试/收摊观测用).
- **Shell 身体**: **统一指令形态**(`docs/orchestration.md`)—— 一条指令只有一种走法: `BodyScanner` 深度 1 切边界(视野只一层, 子 body 里一切原样转发) → 每个标签 = create 实例 + body 字节边到边直投 + 闭合发 eof → **实例自己解释自己的 body**(`say` 当文本, `all` 当子指令再扫一层). 没有"容器走另一条路", 没有解析器认识的魔法名; 词表闸门/lane 闸门/编排参数注入只在 `route` 一处, root 与容器共用. 裸文本旁白(final answer)不进轨不产帧; **Tracer 总账**(原版 MShellEventTracer/pop_frame 同构, 内嵌非 routine): 轨道完成即 trace req 报账(全系统一本, 单调索引), 违规帧走**同一条报账路**(造 `kind='error'` 交 `_record_trace`, 不另开小路); 流收尾 = 帧边界 —— **不等在途**(指令漂着: 原版拍尾只等观测类, 暂全不等)整批 drain 成 obs 回灌灵魂(帧带 **owed** 戳: 错误自带债 / 动作 meta 的 `always_observe`(查询类出厂声明) / 调用处 `observe="true"` 认领, 三者 or 合并, 见"注意债"条); 收尾后到达的 trace 即迟到(即时转发带旧拍号, 灵魂门禁只入史不唤醒).
- **Channel 串行轨**: 队列 + 单消费者, 同串异并是全部语义. **create 在入队口**(shell 只递名字入参, 实例永远在轨的手里), worker 按到达序 start + wait, **完成即报账**(trace req 给发起者, 容器向上汇总 —— 原版 tracer on_task_done 回调同构). **容器延迟入队**(`defer`/`release`: 闭标签才排进 lane, 否则挡住自己同轨的子 → 死锁). 不管名单(闸门在 shell 词表), 不管互斥(动作自带 modules 声明, 锥冲突 kernel 拒绝), **不管结果**(无缓冲无收割 —— 账本在 shell, 轨是纯调度器).
- **Moss 一体**: 组装 ghost + shell(词表装配注入 `verbs`), prompt 片段(`prompts`)拼一次注入 ghost 系统前缀, 接通即放手.
- **Interact 终端 / UI 进程外挂(2026-10-17 落地, 同日 interact 退役)**: 交互面 = 订阅渲染 + declare/stop/do 转发, 契约在 `ui.py`("UI 与灵魂互不认识"). GUI 走 **kernel dial-in**(run.py 开 grpc 口, ZERO_GRPC_ADDR/config 覆盖): UI 进程自带 hub 拨入, pubsub 订阅表在 kernel((namespace,topic)→rid)**跨 hub 路由**; Qt 主线程 + hub asyncio loop 独立 QThread(跨线程 queued signal 桥接, 不用 qasync 硬缝). run.py 纯 server 入口(无终端交互, Ctrl+C 退, 日志独占 console). UI 崩不带走 moss(adapter 掉线重拨); 后续手机端 = 同模式再加 hub.


## 已定决策

- 标签二元名: 第一槽 = **lane 名**(中立记号, 排序跟着叙述走, 不跟资源走), 第二槽 = **verb**(动作 routine 名). 全部 routine 名不带 `moss.` 前缀. **裸动词名 = 默认轨 main 省写**(对齐原版 root channel commands drop the prefix: `<say>` 即 `<main:say>`; 深层轨才必须带前缀).
- **三层分离**: 词表(shell 全局表, 装配注入 + registry 增删) / 互斥(modules 锥, kernel TryAcquire) / 排序(lane, 同串异并). 轨道不管名单不管互斥, 只管同串.
- lane 显式建: 出厂只有 Moss 预建的 main 一条(全串行); 开新轨 = create_chan 动作(经 shell chan 控制面, 普通动作一个, 挂任意已开的轨排队执行), **回执确认后下一轮可用**(同流建完就用会撞 dispatch 查表时序). 词表外 lane 当场 error 帧 —— 并行是有成本的显式决策, 不随取名免费获得; 建轨幂等, 回执带全量轨名.
- 文字与出声分离: 裸文本 = 纯文本旁白(**final answer**, 给用户的回复文本 —— 不进轨不产帧, 记忆的 assistant 原文即是, 将来 UI 出口直接吃 ghost 字流); 出声说话走 say 标签(标签体 = 要说的话, 流式收). 文字回复与语音出声是两条通道. say/create_chan 是身体的结构性动词, Moss 出厂词表自动注入, 不靠调用方记得.
- 闭合只写裸动词名(`</say>`): 轨道前缀冗余 —— 开标签定轨, 闭合配对靠解析器状态; 带前缀的闭合即违规.
- 违规处死肇事标签, 流不级联: 错误闭合处死开标签(内容丢弃不执行), 嵌套违规处死嵌套者(外层存活); 解析器重同步 —— 一次违规一帧错误, 不毒化后续流.
- 动作 routine 是一次性的(create → start → run → wait); 常驻的只有 channel / ghost / shell / moss.
- run 不退出 = 常驻(单例时间轴); 唯一出口是 stop 哨兵, 属框架优雅收割的配合.
- 序的锁链(每跳保序): 字序 = mailbox 序 = 解析序 = shell 转发序 = lane FIFO = 执行序.
- 动作结果回灌: **推送制**(原版 tracer 同构, 见"推送制回灌"条). (演进: 拉取式[worker 收缓冲 → flush 拉走] → fence+grace 收割+后台泵 → 推送制, 两次演进均因"账本该挂谁"的考据推翻前一版)
- **UI 出口 = pubsub 订阅**(zero-py 正统): ghost 把字流与观测帧广播到 namespace `moss`(topic `text`/`obs`), 订阅端渲染 —— UI 与灵魂互不认识(ghost 不持 UI 引用, 订阅端不碰记忆), 换 WebUI/GUI 只换订阅端. 中间形态"on_text callable 穿 req data"已删(框架有订阅机制就不用 hack). (演进: interact 终端订阅打印 → **PySide6 进程外挂**(2026-10-17): kernel 开 grpc dial-in 口, UI 进程自带 hub 拨入, pubsub 订阅表在 kernel 跨 hub 路由; interact.py 随即退役删除, run.py 成纯 server 入口, 日志独占 console)
- 观测帧两种: error(协议违规 + 动作失败, 含锥冲突) / action(动作结果). **say 回执只报执行事实**(played 时长 + words 字数, abort 加 stopped 尾部 —— 原版 played_message/stopped_message 同构): 说的全文 assistant 记忆已有一份(logos), echoes 不复读内容, 只报身体干得怎么样; 裸文本旁白只有 assistant 侧一份(logos), 无执行回执. (演进: 曾回灌 said 全文, 双份冗余, 已删)
- **标签开启即入轨**(对齐原版 command starts on the open tag, 两种形态同一个入轨时刻): 自闭合 = 原子动作(开启直接入轨); 开标签 = 流式动作(开启即 create —— **实例永远在轨的手里**, shell 只拿回执 rid 直投 body 增量, created 态即可收, 边到边收; 闭标签发 eof 收流). **start 一律由轨调度**(同轨 FIFO, 两种形态一视同仁) —— 开标签只是 create, 不越权插队. 结果回收统一走 flush(worker start+wait → 缓冲 → 拉取), 流式与原子同一路径, 无特殊回执机制. 同轨连续 say 天然串行(FIFO), 无锥竞态; 一张嘴的物理播放约束(两路 say 不重叠出声)留 L1 real 引擎的共享播放队列.
- **回声帧循环**(照抄原版 need_observe): 每拍产物是观测(动作回执/错误都算); 有观测 = 有值得回头看的事 → 桥梁当场驱动再看一拍(不带新输入, 记忆里已有原文与观测), 不等用户驱动 —— 错误的反应机会不靠用户再打一行字. 空拍(纯旁白, 无动作无错)收链; 防无限的闸门不是计数上限, 是空拍收链(原版同语义: 模型每帧都发动作就每帧都续, 靠模型收敛). 拍链各拍的 assistant 原文与观测顺序入记忆(自纠过程本身是后续轮次的 few-shot). (演进: 曾用 error 事件 + 自纠一次 + stale 丢弃; 对齐原版时发现原版就是无上限回声帧循环, 已照抄替换)
- **Mindflow 拆分**(何时想归桥梁, 怎么想归灵魂): 独立 routine 持事件泵 —— 外界输入(``user`` 事件)经泵, 回声帧在拍链内自续(不占事件队列); ghost 入口改 ``think``(text 可选 = 统一外界输入, 不再有 ask 概念 —— 灵魂面对的不是"用户的问话"而是"一条输入"). 聚合: 拍启动时排队的多条输入折叠为一拍(原版 input nucleus 同构, 连打两行 = 一拍). **ask 即回**(原版 fire-and-forget 同构: 信号进 nucleus 队列, 泵异步消费, 回复走独立输出通道 —— 原版没有调用方屏障; ask 返回仲裁 verdict). "这轮说完做完没有"用 idle 探针(静默 = 无在途链 + 队列空 + 无挂账; 原版这个反身面走 Mindflow Channel 未抄, 这是最小替身). 心跳(定时思考/感知事件)留 L1 —— 泵已就位, 加事件源即得. (演进: 曾发明 future 链对话屏障"ask 等拍链完成才交回用户", 已删 —— v2 自创缝合处藏了两个真 bug: ①静默态 aside 的 future 挂进无人释放的列表, 下条链直接覆盖 = 调用方永挂; ②屏障把终端锁死在"等拍链完成", 在途拍永远无法被终端新输入抢占 —— 中断语义在真实入口不可达. 删屏障 = 两 bug 同灭 + 中断可达)
- **拍链中断**(照抄原版 attention.abort): 一拍抛错(脑子挂/等观测超时)= 拍链断 —— 桥梁捕获: 泵不崩(原版 `_generate_thinking` except 兜底同构), reason 留灵魂记忆 `interrupted` 帧(原版 `_last_abort_reason` 织进下一帧 moment.previous.stop_reason 同构 —— 下一拍的底, 脑子看得见上一拍怎么断的). ghost `interrupt` 入口由桥梁调用(中断判定归桥梁, 留痕归记忆主人); 留痕请求也失败(灵魂整个没了)只记日志 —— 泵不崩优先. 终端侧 UI 订阅 `interrupt` topic 打断因. (演进: 曾断言"多源 challenge 仲裁不抄 —— 终端单源无竞争者", 已被推翻: 项目方向多模态(L1 语音/机身), 多源竞争仲裁照抄, 见下条)
- **多源仲裁照抄**(原版 challenge 体系, 多模态前提 —— L1 语音/机身多源并发输入必须仲裁): 外界输入 = Impulse(id/source/priority/strength/complete/mode/衰减/保护期), 在途链 = Attention. 判定分支序照抄原版 `_challenge_attention`: strength=0 → yielded(最先短路, 绝不竞争)→ stale → FATAL(win/aside→buffered)→ BACKGROUND 无权插队(notify→buffered 否则 suppressed)→ **同 id 尾包吸收(FATAL/BACKGROUND 之后, 保护期之前)**→ 保护期 → 强度仲裁(同源挑战者 ×1.1 提权)→ 同 id 未 complete → absorb(首包挂起等尾包, 尾包折进). preempted → 在途拍走拍链中断同一条通路留痕收链(ghost `abort` 事件置位 + think 三检查点协作退出 —— req 无取消传播, 不能 task.cancel, 原版 attention.abort 立即生效同构), 挑战者开新链. buffered/queued/suppressed 都进挂账(原版 inject_percepts 同构): 本链下一拍优先看, 链收尾无人接盘则 run 循环入队合成 impulse 开链消化 —— 消息永不丢. next 强制续拍(原版 forced next frame 同构). 偏离点(均有据): ① suppressed 与 buffered 同归宿(都挂 inject), nucleus 冷却不抄(排队本身已是等待), 被抢占的挂起链 requeue 置 complete(不等尾包); ② **吸收不受保护期挡**(原版也挡, 但原版 suppressed 走 nucleus.suppress 缓冲重试兜底, 塌缩版无 nucleus 挂账不重试 —— 不提前则挂起链等的尾包被挡 = 永挂, 提前是 nucleus 重试的替身); ③ **首包不与异包折叠**(聚合只折完整输入: 未完输入混进聚合会错戴最新者的 id 等一个不来的尾包 = 永挂; 原版由 nucleus 按源隔离聚合、内部配对首尾包 —— 塌缩成参数的替身规则), 首包开链时同 id 尾包当场吸收、完整邻居进挂账; ④ **未完输入落败不挂账**(尾包才载全量, 挂前半句只会与尾包重复; 尾包不来 = 这句没说完, 不算丢消息); ⑤ 挂账加上限 100 丢最旧(原版无上限同坑, 这里加闸防 DoS). (演进: 偏离点①"suppressed 进挂账"与"requeue 置 complete"已被档2 推翻 —— 原版源码证据 `_fire_challenge` 只有 buffered/queued 才 inject_percepts, suppressed 的 messages 留核缓冲等下届自己赢; ②③④保留为替身规则, 见下条)
- **仲裁时序对齐(档2 完全对齐, 2026-10-16 用户拍板)**: 入场时仲裁 → **拉取时仲裁**的时序倒转(原版 rank 在裁判循环拉取时打, 不在 declare 入口打 —— 塌掉的不是参数是时序). 结构: 检录缓冲 `_pending` + 信号线 `_has_signal` 替 asyncio.Queue(原版 nuclei buffer + `_has_impulse_event` 同构 —— 核只点灯不推货); 裁判循环 = 永久 task(信号即醒 + 0.5s 空搏动保活, 原版消费循环同参 —— 原版超时**裸 continue 不打 rank**, 重赛由事件驱动: 新 declare 点灯或链终点灯[原版空缺期 thinking 循环 1s 扫描的替身]); 链 = fire-and-forget task. 语义: ① declare 纯申报登记(verdict 只剩 accepted/dropped[空knock]/noop[打断冷静期]/ignored[stale], 仲裁结果调用方不可见 —— 原版返回 None 同构); ② **default 输家 suppress 回检录缓冲重赛**(原版 `nucleus.suppress` 同构: 不挂账不陪葬, 下届独立成链 —— 下个事件点灯的 rank 顺带重比, 原版"新信号顺带扫冷却核"同构); ③ **单源成链**(原版一巡只出一个 impulse: 跨源不并拍, 同源仍聚合连打两行一拍); ④ rank 群比补齐(原版 `_rank_best_impulse_from_nuclei` 同构: 字典序 priority→strength, 平手特殊核优先再先到先得; **尾包快车道** 原版 `_AWAITED_IMPULSE_LANE` 同构 —— attention 挂起在等的同 id 尾包无视优先级/强度直接胜出, 且**不与同源邻居聚合**: 原版 nucleus 每巡只出一条, 尾包带全量并进邻居即污染, 邻居另立候选输家回缓冲); ⑤ 抢占赢家回检录缓冲头(链判死与收尸之间的巡场窗口, 赢家已被 drain 不放回就丢 —— 回插缓冲头, 链终 _wake_or_idle 再点信号); ⑥ 被抢占的挂起链 requeue **保持 incomplete**(残句回检录缓冲等尾包 rank 批内配对, 原版核内部协议同构 —— 强想半句不如等它的尾包); ⑦ `woken` 区分信号/搏动(静默态 aside 不建链 = 原版"送达不动脑"; 空闲瞬态消化拍只认信号; 正文挂账消化只在链终 —— 防 0.5s 搏动自激循环). (演进: 档1 最小对齐方案被否 —— 用户拍板"完全对齐"; ⑦"空搏动自激循环"所防的 tick 重比后经源码重读被整体推翻 —— 原版超时裸 continue 不打 rank, 已撤, 见"空搏动保活对齐"条)
- **空搏动保活对齐(2026-10-16 用户拍板"那就对齐")**: 重读原版源码修正此前误读 —— 原版定时器共三处: ①裁判循环 0.5s(超时裸 `continue` 不打 rank, 纯保活); ②thinking 循环 1s(现任空缺/已死时主动 rank+补点灯, 无货置 idle —— 空缺期扫描器); ③执行期提权心跳 1s(`_escalation_interval`: 刷新 `strength_refreshed_at` 把现任强度**钉平台值** —— 执行期永不衰减翻盘, 注意力专注性本体). 原版重赛真实触发 = 新事件点灯的 rank 顺带扫冷却核 + 空缺期扫描; suppress 冷却/强度衰减全是惰性时间戳(peek 时查, 从不定时唤醒). mossv2 对齐: `_arbitrate(woken=False)` 只保 `_maybe_idle`(idle 判定 —— 原版在 thinking 循环, mossv2 无此循环, 挪到裁判循环空搏动), 撤 rank/challenge —— 翻盘只经新 declare 点灯或链终点灯(链终 `_wake_or_idle` 即原版空缺期扫描替身, 行为等价). **残余偏离(有据, 待定夺)**: 原版执行期提权钉死 → 挑战者在拍内**永远**赢不了(除 FATAL/interrupt); mossv2 无提权心跳, 替身=保护期 —— 保护期后拍内新事件挑战遇衰减现任**仍可翻盘**(撤 tick 后仅剩此路径). 补提权(拍内钉死)会改 challenge 语义, 现无测试依赖此窗口, 留作后续决策.
- **一个 Ghost, 脑子是外设**(brain 不是第二个灵魂): 只有 Ghost 一个类, 脑子经 `brain_factory` 类属性注入(与 Say.engine_factory 同构 —— 换脑不换魂的组件式); brain.py 只有 Brain 组件(LLMClient 接线 + 记忆→input items 转译), 不继承 Ghost. 文件分界 = 依赖分界(LLMClient 重依赖集中在 brain, 轻量测试不装也能跑)+ 变更分界(转译是 LLM 协议的事, 不是灵魂的事). (演进: 曾有 WiredGhost(Ghost 子类)重写 `_brain_stream`, 两个 ghost 类功能重复, 已删)
- prompt 片段 = 一次性 routine(`prompt/xxx`, 斜杠即命名空间), run 产文本. Moss 装配时按名单拼**一次**注入 Ghost 系统前缀(前缀缓存友好, 不每轮重拼); 排序纪律: 稳定在前(persona)易变在后(capability). 不做前缀发现, 热注册后不自动刷新(要新前缀就显式重组).
- **记忆 = 感知关键帧(原版 ``blueprint/moment.py`` 同构, 塌缩版)**: ghost 内部 ``Moments`` 观察器(非 routine)持有帧链 —— 每拍一个 ``Moment``: ``previous``(缝合缝: 上拍动作回执 + 停止原因)/``percepts``(本拍输入, 按源 keyed)/``dynamic_context``(瞬态快照, 永不入史)/``logos``(本拍输出). 原版 Echoes docstring 的存在理由: "no current model supports full-duplex interaction, this adapter layer stitches the pieces back into one exchange" —— 世界碎片化而模型只认完整交换, 帧是缝合层. 为什么在 ghost 不在 mindflow(原版挂 mindflow): 原版是单体架构(同进程函数调用); 塌缩到 routine 边界后帧的产生处恰是 ghost.think, 记忆是灵魂的实体. 滑窗按帧计(max_moments 默认 100 = 原版 max_moments_size 同参), 出窗即忘, 不修齐回合边界(残头是已知代价, 原版 to_history_turns 同). ``history_turns()``(原版 to_history_turns): recap 置顶 + 各帧顺序渲染 + pending 回声尾缀(链刚收完/中断时立即可见, 缝进下一帧后归位不双份) —— 脑子 feed 与 recall 同一份. **反幻觉缝合**(原版 executed_logos 同构): 中断拍已执行的部分 logos 也落帧 —— "执行了但模型没说, otherwise they become pure hallucination". 瞬态槽(原版 dynamic_context 每帧重取 + percepts 同源覆盖合一): ``set_dynamic`` 同源覆盖, feed() 尾拼 ``[源] 文本``(模型每拍看到各源最新"现在", 历史零膨胀), for_saving/history_turns 都丢(原版三处同丢). (演进: 曾是平铺 dict 条目 + Memory 滑窗按条目计 —— 被帧结构替换: 平铺版没有缝合缝/按源 percepts/反幻觉, 三者都要帧这个单位才放得下)
- **瞬态快照(原版 dynamic_context + percepts 同源覆盖, 合一)**: 状态类输入(感知快照: 电量/位置/ASR 渐精包)经 ``declare {transient: True}`` 入场 —— impulse 带 transient 标志, fold 分流(不进聚合正文, 同源后到覆盖先到; 队列里只有快照 = 纯瞬态拍, 正文空不开 user 条目), think 的 ``transients`` 入 Moments 瞬态槽(同源覆盖永不入史, feed() 尾拼). 仲裁输了也最终入槽: 瞬态挂账 ``_pending_transients``(同源覆盖, 源数有限天然有界), 本链下一拍/合成拍带上 —— 槽里永远该是各源最新值; idle 判定含瞬态挂账. transient+aside = 后台状态刷新不打断思考(不开链, 只等下一拍入槽). 失败侧不进正文挂账(挂旧快照无意义).
- **Epoch 压缩(recap, 原版 new_epoch: compact + recap)**: ``Moments.new_epoch(recap)`` —— 帧链出清转生 recap(dropped 不涨: 压缩不是忘), recap 置顶渲染(history_turns 首条, 下一 epoch 才被替换 —— 原版 epoch 循环同语义), epochs 计数. ghost ``epoch`` 入口用脑子生成摘要(走 brain 同一 ``stream`` 协议: history=回合制条目, system=RECAP_SYSTEM 摘要指令), 空 recap/空窗/摘要失败不动作. **原版供料未接线**(with_epoch_recap 全库无注册方, 运行期只在首拍开一次空 recap 的 epoch), 我们把通路接通(青出于蓝的一处), 运行期自动触发留待真被 100 帧打穿(心跳/装配方谁都能调). brain 转译 recap → ``[recap] ...`` user 消息(尖括号全角化防回声复读).
- 原版对照: 原版轨即锥(两坐标系合一, 靠建模纪律); 我们两坐标系分离(声明式). 跨域动作(左手+尾巴式)声明锥并集、原子占用, 挂任意轨.
- **八核对齐审计定版(2026-10-15, 逐核对照原版源码)**: input✅(_fold 聚合+首尾包吸收) / notify✅(mode=notify 挂账) / aside✅(mode=aside) / cell_event✅(BACKGROUND+notify 参数组合, 原版本就"同构 notify"无专门机制) / broadcast✅(FATAL+aside 参数组合, 原版 ImpulsePrimitive.broadcast 本无专门核) / interrupt🔶→✅ / knock❌→✅ / command❌→✅(三缺口同轮补齐, 见下条). **可扩展机制 = 参数面即扩展面**(NucleusMeta 类注册的塌缩替身): 新感知源 = 新 routine 调 declare 参数组合, 三级递进 —— ①同策略新源零改动 ②新策略加 mode 枚举值/轴(小改), 两个以上源共享同一非默认策略时提升 per-source 策略槽 ③新执行路径才动"怎么想"边界. 核类生态**不恢复为独立时间轴单元**(单消费者判据: 源是 routine, 海关不是 —— 原版 nucleus 是泵 rank 同步 peek/pop 的被动数据结构, 无独立时间轴, rank 原子性要求核缓冲与 attention 立即可见); 核的**声明面与治理面**已按上面"nucleus 恢复"条重建为 ``nucleus.py``(仍是被动对象, 只在 declare 被问一次).
- **纯打断(原版 interrupt nucleus / ImpulsePrimitive.interrupt: FATAL+notify+effort='none'+interrupt=True)**: ``declare {interrupt: True}`` —— 优先级强制 FATAL(原版"语义承诺不可降级"), 不参与仲裁(有链必停, 闲时照样开空拍 = 原版 trivially attended). 停 = 在途拍 abort 留痕 + 开一帧**空拍**(record_only: 只缝留痕落 percepts, 不思考不出声不等观测) —— 携带的 text 由空拍 percepts 入史(原版"可携带 messages 解释中断原因, 由下一帧 percepts drain"同构)但**不被回应**("停"不是新话题). 胜利侧冷静期 0.5s(原版 suppress_seconds 同参): 冷静期内再敲 verdict=noop 静默丢(原版 add_signal 静默丢同构) —— 打断无累积语义, 多次等价一次. ghost 三拍形态: 正常拍(脑子产流)/反射拍(logos 直投)/空拍(record_only), 全走 think 一个入口. 终端入口 ``/stop``. **2026-10-18 拆轴**: 两轴正交 —— ``interrupt`` 只管"停身体"(抢占 abort), 答不答看 ``effort``('normal'/'none'); 裸写 ``declare {interrupt: True}`` 仍自动补 ``effort='none'``(本条即原版 primitive 全组合, 向后兼容), 要 barge-in 就显式 ``effort='normal'``(见 ASR 条"已对齐").
- **knock 可丢(原版 knock nucleus, 八核唯一 losable)**: ``declare {mode: 'knock'}`` —— 仲裁输了**即丢**(verdict=dropped, 不挂账不重试: 端侧自己持有内容, 门没敲开就再敲一次; 原版 suppress 清 cache 同构); 空文本当场丢(原版"没东西可宣告"); 赢了走真实思考(原版 default mode impulse). 与"消息永不丢"哲学的关系: knock 是**受控例外**, 由调用方显式选择可丢语义 —— notify 仍是不丢的那条路. 偏离(有据): 排队的多条 knock 与正文同拍折叠(原版分核 rank 单选; 与 input 聚合同判据, 挂账排队窗口极窄).
- **反射弧(原版 command nucleus / ImpulsePrimitive.command_only)**: mindflow ``command {text, priority}`` 请求 —— text = logos(CTML)直投身体**不经思考**(原版 effort='none' early-return), 无脑也能跑(急停不需要智慧); priority 原样继承(原版无 floor, priority=5 = 原版 fatal_command). last-wins(fold 抽取: 最新指令赢, 旧指令作废 —— 原版 last-impulse cache); 仲裁失败即丢(原版"指令无重试, 失败就丢等下一条"); 被遮蔽正文挂账由下一真拍消化(消息不丢不抢反射弧). 执行的 logos 落帧入史(反幻觉: 执行了就必须看得见, 原版 executed_logos 同构); 反射拍**无回声评说**(原版 effort='none' attention 全程不语: 链收, 结果留给下一届缝合缝). 终端入口 ``/do <ctml>``.
- **nucleus 恢复为「源治理单元」(2026-10-18, 用户"nucleus 这个概念没了吗, 我觉得还是需要的")**:
  核类生态塌掉后, 源退化成 ``Impulse`` 上的一个字符串 —— 行为都在(declare 参数组合),
  但**身份没了**: 缺省参数/聚合策略/失败处置/体检信息散落在各调用方, mindflow 只认得
  一个 ``source``. 恢复的是**声明面与治理面**, 不是时间轴:
  - ``nucleus.py`` 的 ``Nucleus``: ``name/description``(自解释) + ``aggregate``(同源是否折一拍)
    + ``suppress_seconds``(输了之后的降频窗) + ``defaults()``(该源缺省申报字段)
    + ``govern(imp)``(同步治理, 返回 None = 丢) + ``on_suppress(imp, by)``(原版 suppress 回调)
    + ``status()``(体检一句);
  - ``NUCLEI`` 注册表(原版 ``NucleusMeta`` + IoC factory 的替身): 类注册 + 按名构建;
  - ``declare`` 三步: **核缺省 → 同步治理 → 检录**(原版 ``Nucleus.add_signal`` 的同步核);
    ``MindflowInput.nuclei`` 选核(空 = 全部默认核), ``req nuclei`` 自解释面.
  **不恢复**: 原版"每核一个缓冲 + 独立消费循环 + peek 群比" —— rank 原子性会把群比变成
  分布式一致性问题(核仍是被动对象, 只在 declare 这一刻被问一次). 出厂四核
  user/asr/command/mindflow; **核不是准入门槛** —— 没注册核的源照样裸参数申报.
  冷却只在窗口开启时设一次(mossv2 输家回缓冲重赛, 每次续期会把冷却无限延长);
  ``govern`` 必须同步(耗时治理由源 routine 在申报前做完, 原版同要求).
  核也能接管**赢家侧**: ``on_attended(imp)``(原版 ``attended(impulse)``)在成链前被问一次
  (mindflow ``_start_chain`` → ``_attend``), 可返回改写后的冲动 —— 原版 listener 正是用它
  把抢到 attention 的提权档降回运行档(见 ASR 条"onset 四规矩").
- **打断收尾(原版 thinking 因 abort 退出即 shell.clear 同构 —— 修既有隐患)**: ghost think 异常路径(被抢占/打断)补发收尾令 ``{abort: True, beat: N}``: shell 解析器重开(旧患: 在途拍被抢占从未收尾, 未闭合标签张着嘴吞下一拍的字 —— 新链的字会落旧流的嘴里) + 处死未决流(定点走轨) + **轨打断**(channel ``abort``: 未 start 的 destroy = 原版 clear 取消 pending tasks; 在跑的 stop —— 动作自己的 stop hook 优雅收场, run 正常返回照常报账; 不收场的 STOP_TIMEOUT 兜底杀 = 原版在跑任务 fail(INTERRUPTED) 同构, 见"打断原语生命周期化"条) + 已完成结果 drain 入史(反幻觉). **拍号门禁**(beat nonce + 拍开闭窗口): 观测回执带拍号, 且只在**本拍等待期**(think 体内)被收 —— 迟到/异拍的回声只入史不唤醒新拍(收尾后到达的旧回声不得冒充新拍的观测); 在岗等待期到达的迟到帧就地消化(原版 :1148 同构); 空观测照样唤醒(身体收尾 = 拍完了, 空回声帧靠它收链). shell ``_finish`` 两种形态: close(自然收尾) / abort(打断收尾, 先杀后收) —— 两者同走不等在途 + 迟到转发(见"指令结果不触发"/"推送制回灌"条).
- **全异步执行(2026-10-16 用户拍板"现在就对齐", 原版 "commands keep running in background" 同构)**: 长动作跨拍存活 —— 上轮启动的长时动作不被下轮打断, 下轮照常思考. 原版考据定音: 三循环并发(action_loop 独立于 articulate_loop, `ghost.md:70`), 命令完成只 `add_echoes([], need_observe=True)`(`mindflow_in_shell.py:186`), **迟到结果不唤醒任何新 attention** —— attention 在 need_observe 清零后就地结束(`_generate_thinking:1148`), 迟到回声进 pending, 由**下一届首帧缝合缝读到**. 初版对齐(fence 收割 + 后台泵)后被"推送制回灌"条整体取代, 行为语义(收束窗同拍/超窗跨拍/迟到入史不唤醒)又被"悬等在途"条改写(撤窗: 在途必入本拍). 15s think 超时保留为真收尾异常的兜底. (演进: ① channel 派单 seq + flush(fence+grace) + 按轨后台泵 → ② 推送制 trace req + shell 总账, 见下条 → ③ 撤收束窗: close 悬等全部在途, 见"悬等在途"条)
- **推送制回灌 + Tracer 内嵌 shell(2026-10-16 用户拍板"改")**: 重读原版定音 —— 原版数据面本就逐条推送(命令完成即 tracer append 事件 + 点 need_observe 旗, `shell_trajectory.py:539-564`/`mindflow_in_shell.py:186-191`), 消费面仍帧边界批量(`pop_frame` = peek + commit 一次 drain 全部积压成一帧, `:703-707`), 且**账本全系统一本挂 shell**(tracer 嵌在 MShellTrajectory 以回调挂 shell, 非独立服务). mossv2 此前是收割制(一轨一本 `_results` + 逐轨 flush + 按轨泵 + fence 水位) —— 三套机器本质都是在为"账本跟着轨走"付利息. 对齐: ① channel 退化纯调度器(同轨 FIFO + 完成即 trace req 报账, 结果面全删 —— req 并发 handler 不排 mailbox 序, 收尾等窗期间照常到达[send 会自锁等窗]); ② shell 内嵌 Tracer 总账(全轨一本, 单调索引; 违规帧同入账; **内嵌非独立 routine** —— 三无判据: 无独立时间轴/无隔离/无第二消费者); ③ 帧边界 drain(close/abort 时整批出账成 obs, 一帧消化整批); ④ 迟到 trace(收尾后到达)即时转发带旧拍号 —— ghost 门禁升级为"拍号匹配**且拍开着**"(think 体内才收): 在岗等待期迟到帧就地消化(原版 :1148 在岗消化同构), 已收/异拍只入史+pending, 下一届首帧缝合缝读到; ⑤ **收束窗 0.5s 保留但搬家**(shell 等在途报账, 非 channel flush): 快动作确定性同拍回收保住回声帧循环, 真长动作超窗跨拍存活 —— 近失竞态(完成 vs 收尾毫秒差)原版同样有(窗口=查旗瞬间), 我们的 0.5s 反而更宽容. 退役: channel 结果缓冲/`_unresolved`/`_done_cond`/flush/fence/seq、shell 按轨泵/`_pump_q`/`_lane_seq`/帧箱(`_frames` 并入总账). ghost 侧最小改动: 拍开闭标志一个(门禁窗口), think 三出口推进. 已知疣(修掉): 泵等整个 ≤fence 前缀 settle —— 同轨快单结果被慢单扣到其 settle(idle 期无下一拍 flush 来捞); 推送制下账即时到达, 疣不存. (演进: ⑤ 的收束窗 0.5s 已被"悬等在途"条撤销 —— observe 旗缺位下改悬等全部在途)
- **悬等在途(2026-10-16 用户拍板"先只修0.5s这个, observe 的不管")**: 撤 0.5s 收束窗 —— close 悬等**全部**在途报账落账(observe 旗缺位的过渡一刀切: 不分观测类, 原版只悬等 always_observe 类且无时限、非观测类后台跑). 动机: 用户报真 bug —— query_weather(2s 级 API)超窗 → 迟到只入史 → **没有下一轮**(模型永远看不到天气); 且 0.5s 把"谁被看见"外包给时间运气(同一条 say 0.4s 完成多想一拍/0.6s 完成不想, 原版判据是结构性的 always_observe 注册元数据, 用户点名"不应该受时间影响"). 对齐后: 回执必进本拍 obs(动作时长 ≤ think 15s 即悬等全程), 回声帧当场消化, 行为不受完成时刻影响; 残余迟到只剩 stop 截断回执/防楔顶放行残余. **防楔顶 ``INFLIGHT_CAP``=18s**(≥ think 15s + 收尾余量): 在途卡死时 ghost 的 abort 消息(15s 超时后发)排在自旋后面, 无顶会楔死 mailbox —— 它是防楔兜底不是语义窗口. >15s 动作走 think 超时 teardown → stop(截断回执迟到入史). 已知过渡疣(observe 旗落地即消): ① 快 say 也悬等并触发回声拍("听见自己说话"多想一拍); ② 打断/下一拍要等在途动作收尾(最坏 ~15s); ③ 长动作跨拍存活仅剩超时路径. observe 旗(动词 meta 声明观测类: 悬等只等观测类 + 回声帧只认观测类回执)为已定待做项. t_fence_no_steal(跨拍不串台)随撤窗退役 → t_multilane_same_beat(跨轨同拍收割). (演进: 实跑暴露反向病 —— say 4.4s 被悬等落账触发"听见自己说话", 全员 observe=True 恰与原版默认相反; 本条整体被下条"指令结果不触发"改写: 悬等段/计数/防楔顶退役, "不受时间运气影响"方向被继承且更彻底 —— 快慢都不触发)
- **指令结果不触发(2026-10-16 用户拍板"不对, 我要的是指令结果不触发" + "obs依然记账, 只不过不驱动mindflow")**: 原版考据钉死**默认指令结果不激活下一拍** —— 回声帧循环 `_mindflow.py:1148` 只看 need_observe 旗, 非观测命令(say 未标 always_observe)完成连旗都不点; 拍尾 `wait_tasks(to_be_observed=True)` 只等观测类(`interpreter.py:665` 过滤), 随后的 `wait_stopped` 等的是解释器停止事件非等任务跑完 —— say 类长命令拍尾**无人等**. 用户定夺暂全不等(激活途径后议). 对齐: ① shell 撤悬等段 + `_pending_acts` 在途计数 + ``INFLIGHT_CAP`` 整体退役 —— close 不等任何在途, 指令漂着(拍收后回执迟到转发入史); ② **obs 链路整体不动**(报账/帧边界 drain/入史/pending 渲染照旧 —— 记账不断, `history_turns` 尾缀保证迟到回执立即可见), 唯一改动在 mindflow `_chain` 回声帧判定: ``r.get('obs') or self._queued_beat`` → ``self._queued_beat`` —— **obs 内容永不是自续依据**, 回声帧唯一驱动 = next 插队(显式声明); ghost 零改动(拍号门禁/迟到入史原样). 语义: 指令结果的到达不是激活源; 落账 ≠ 激活(回执/错误经 pending 入史, 下一届首帧缝合缝被动读到 —— 原版 pop_frame 水位 drain 同构). query_weather 的下一轮暂无激活途径(已知代价, 用户拍板): 模型要看结果需显式声明 —— 原版两途径 wait="observed" 流内声明(CTML 属性面已通待接线) / always_observe 注册旗, 落地时回声判定从 `_queued_beat` 换成声明集合.
- **注意债落地: 回声与错误统一成"需要被注意的结果"(2026-10-17 用户"本质都是需要被注意的结果")**: 判据从"声明集合对账 + 计数 + 有界等待"退化为**帧上一个布尔 `owed`** —— ① **产帧处盖戳**(shell `_record_trace`): 错误帧自带债(`kind=='error'`, 协议违规同), 成功帧只有被认领过(`observe="true"`: dispatch 时剥离控制属性, 按回执 rid 记 `_observing`)才有债; ② **点数**(ghost): 一批 obs 里有 owed → 拍开着则本拍 `echo=True`(仍走 `_think` 的 echo 分支), 拍已收则敲裁判 `beat`(债晚到也兑付); ③ **点灯**(mindflow): 仍是 `_request_beat` 那一盏 —— 链活同链续拍免擂台, 链死落成下一届首拍(检录缓冲空则自登记空 impulse 开链). 灵魂敲裁判是这条链上唯一的反向控制边 —— 灵魂是结果帧的唯一过路点, 裁判不看账本只被通知; 裁判地址由灵魂从 `think` 请求的 `source` 里读(谁问我思考谁就是裁判), **不留启动期自报握手**. 语义收益: ① 错误不再靠"下一届碰巧有人说话"才被看见(模型当场纠正, 原版 >=400 强制观测的实质); ② 未声明的成功仍不打扰(不引入"每条回执都想说话"的噪声); ③ "5 个观测是否 5 拍"由完成时序决定(扎堆落在同一帧窗口 = 1 拍, 错峰 = 各自一拍) —— 与"灯亮即消费"的原版行为同构, **不需要计数器**. **血统对照(逐条考据, 2026-10-17)**: 同构四项 —— ① 灯=布尔闩锁幂等置位(`Echoes.need_observe`
moment.py:63/:100-101); ② 帧上带注意标记(`ShellKeyFrame.need_observe` shell_trajectory:383,
原版由 `_on_moments_observing` 搬进 `moment.previous` mindflow_in_shell:180-183); ③ 消费即换容器
灭灯(`observe()` moment.py:722-731); ④ next 输家与命令完成点同一盏(:717 / :186 回调). **替身两项** ——
① "推入改搭车": 原版 `when_need_observe` 回调直点共享 `moments_observer`(同进程对象图), mossv2 三
routine 只能靠消息, 故信号搭帧走、灵魂解包再点灯; ② 裁判控制边(`req 'beat'`, 地址从 `think` 的
`source` 读, 无自报握手), 原版同一对象图回调直调、无此边 —— 消息化的必然代价, 已压到最小.
**真偏离一项** —— "敲裁判补拍": 原版灯亮 + attention 已退 = `_loop_attention`
只认 `_pop_new_attention_queue`(:974-981, 灯不建 attention) → 进 idle 等下届首帧 `observe()` drain;
mossv2 当场开一拍(内部自动 /go). 理由: 原版 `wait="observed"` 是解释器**无时限阻塞等**
(`wait_tasks(to_be_observed=True)`), 拍根本不收尾、回执压根不迟到; 流式模型阻塞不了, 只能拿补拍
兑现同一承诺. 若将来要严格对齐, 该改的是"让拍等"而非"删补拍".
不做: 错误码分级(2xx/3xx)、`always_observe` 注册词表(接口留白)、think body 里有界等待(不再需要 —— 迟到有 beat 兜底). 已知边界: 反射拍/打断空拍即使有债也不评说(原版 effort='none' 全程不语, 债留下一届); `_observing` 里永不完成/被取消的 rid 靠 abort 收尾自然失效, 不做 TTL. 测试: t_observe_wait(1.2s 迟到债补拍 + 回声拍看得见回执) / t_verb_failure_isolated(error owed、未声明成功无债) / 协议违规三用例补空拍脚本; 38 用例并行 ALL PASS(102s).
- **打断原语生命周期化(2026-10-16 用户拍板"stop就是优雅停止")**: 打断在跑动作从自创 abort 消息旁路改为框架生命周期原语 ``handle.stop`` —— 用户纠偏定音: stop 本就是优雅停止(实例自己的 stop hook 收场 → main task 正常退出 → run 返回值照常报账; STOP_TIMEOUT=3s 超时才 cancel 兜底), 之前"受理语义: 不识 abort 则跑完"实为拿兜底路径冒充主路径的错判 + Say stop hook 写残(只截声不收流, 走 stop 必掉超时兜底). 原版考据反证受理语义本就偏离: interrupt 首帧 → `shell.clear()` → `stop_interpretation` → `interpreter.close(cancel_managing=True)`(`ctml_shell.py:639`) → **在跑任务全部 fail(INTERRUPTED)**(`interpreter.py:562-565`) —— 原版打断 = 处决在跑, 快动作在 stop 收底下(<STOP_TIMEOUT)照常完成 = 受理对快动作事实保留. 对齐: ① channel abort = 队列 drain 后逐个 ``destroy(fire=True)``(未 start 撤销 create, 无账 —— 修掉 created 实例两侧悬挂泄漏疣) + 在跑 ``stop(fire=True)``; ② 新增 channel ``kill`` req(定点处死: shell 处死未决流用 —— handle 在轨手里, shell 只递 rid, 谁创造谁销毁); ③ Say 补全 stop hook(置 aborted + 截声 + 收流 → run 返回截断回执), 撤掉 abort 消息分支(消息面只剩 delta/eof); ④ shell 记 ``_stream_lane``, close 定点 kill 走轨(abort 收尾全轨打断本就覆盖, 无需定点); ⑤ 对账: destroy 无账, kill/abort 回执带丢弃数, shell 扣减 ``_pending_acts`` 防收束窗白等.

## 落地记录(按时间, 与 ROADMAP 的时间线对应)

- [x] CTML 解析器(6 token, 违规即抛, 增量喂入)
- [x] 分发闭环: 同轨串行 / 跨轨并行 / 旁白 / 违规帧
- [x] Ghost / Shell / Moss 分层
- [x] channel 挂载模型(chan + routines 装配, registry 增摘) —— 已被中性轨设计取代
- [x] meta 对齐 zero-py(BaseModel + input_schema, 描述调用契约)
- [x] 中性轨 + 全局词表: lane 懒建/中立名, verb 闸门全局化(registry 迁 shell)
- [x] 动作结果回灌: worker 缓冲 + flush 拉取 + 帧箱回灌
- [x] 能力面注入: prompt/ 片段 + Moss 组装注入; capability 按锥分组渲染(shell 词表 + kernel 路由表 modules 声明, 一份声明喂两张嘴), 跨分支动作单独"跨模块"段
- [x] 动作 modules 声明 + 锥冲突 e2e: `modules()` 类方法声明锥, kernel TryAcquire 原子占用; 跨轨冲突 → StartError → error 帧回灌(test_mutex 覆盖祖先后代互斥/锥不交并行/左手+尾巴锥并集原子拒绝)
- [x] 真脑接入: Brain(brain.py, `stream` 接 agents/_core LLMClient, 两协议同吃; model=xiaomi-mimo/mimo-v2.6-pro —— 2026-10-15 ds 服务挂换 mimo-pro; system→instructions, 记忆→input items 全量, obs 帧文本化; assistant 侧入记忆); prompt/protocol 教 CTML 语法
- [x] 真 LLM 一轮实跑(deepseek-flash): 1.7s 一轮, 模型首答即合法 CTML(说话+`<a:echo msg="你好"/>` 混排), 参数到达动作, 结果帧回灌
- [x] 提示词充实(参考原版 dolores): persona 交互礼仪(行先于言/言行相配/结果不编造等 7 条) + protocol 两段律负例教学(smoke 实测修掉 `<a:greet:wave/>` 三段式错误) + capability 参数签名随行(meta input_schema → `wave(hand, times=2) — 挥手`, 一份声明喂三张嘴); smoke 四轮验证: 参数直达/同轨串行/能力复述不编造
- [x] 旁白不回灌(对齐原版 echoes 语义): shell text token 不进帧箱, obs 只剩 action/error; 自己的话一份在 assistant 记忆 —— 修掉逐块 aside 帧复读(双份冗余 + 长回复碎片化)
- [x] lane 显式建(告别引用即存在): shell dispatch 只查轨表(未建 → error 帧带建轨提示), chan 控制面(create 幂等/list), create_chan routine 走 main 轨排队建轨, Moss 装配预建 main; smoke 四轮铁证: 模型先建轨后并行(left/right start 同毫秒真并行), 串行意图全放 main(tail.end == left.start 完美衔接), "回执确认后下一轮"时序模型自己会说
- [x] 文字与出声分离: 裸文本 = 纯文本旁白(**final answer** —— 给用户的回复文本, 不进轨不产帧, 记忆的 assistant 原文即是, 将来 UI 出口直接吃 ghost 字流); 出声说话走 say 标签, 闭合只写裸动词名, say/create_chan 出厂词表注入, 违规处死肇事标签 + 解析器重同步(一次违规一帧, 不级联). 演进三段: 旁白不回灌(echoes 语义) → 违规化(一切输出皆标签) → final answer 定论(文字回复与语音出声两条通道)
- [x] 流式 say(标签开启即入轨): 自闭合 = 原子动作(开启直接入轨, `_pend` 待发槽删除 —— 原版佐证: start token 即建 task, 无暂存), 开标签 = 流式动作(开标签入轨即 create, body 增量直投实例 —— created 态即可收, 边到边收; 闭标签 eof 收流). **start 一律由轨调度**(同轨 FIFO, 两种形态一视同仁), 实例永远在轨的手里(channel 入队口 create, shell 只拿 rid 投流), 结果回收统一走 flush(observe/等待机制全删, 流式与原子同一路径). channel worker 从 call 简化为 start+wait. smoke 真跑铁证: 同轨 wave 没跑完 say 不开口(say.start == wave.end 同毫秒), 排队期间增量已被 created 实例收好(首 delta 与 created 同刻, 远早于 start), 分轨嘴手真并行(say.start < wave.end, 模型自己学会"main 做动作 b 说话")
- [x] 真声 say(引擎三点契约): Say.engine_factory/player_factory 类属性注入(默认 silent 零依赖, 测试确定性; 老 moss 同构), RealSay 注册覆盖即真声(懒 import 公共 tts 包: SeedTTS 流式合成 + LocalSpeaker 三级流水线本地播放, 需 SEED_TTS_API_KEY). 播放与打断竞速: eof → 播完才算说完, abort → 截声丢缓冲. **一张嘴 = 模块级播放锁**(跨轨 say 并行调度但出声不重叠, 先到先说). smoke 真声两轮铁证: 闭合裸名一次写对, TTS 首包 ~300ms 边合成边播, said 回执完整; 已知噪音: shell slow mailbox callback 警告(on_message 同步等播放收尾, 属 ask 屏障"说完才算说完"语义, 串行对话无实害)
- [x] 终端聊天入口(interact + run): interact routine 订阅制(zero-py pubsub) —— `@subscribe(namespace='moss')` 拿 ghost 字流(topic text, 边收边打 CTML 原文)与观测帧(topic obs, 逐行), stdin 一行一轮(ask 即回, 不锁终端 —— 用户随时能插话, 在途拍可被新输入抢占 = 中断在真实入口可达); run.py 纯装配(Moss + Ghost(brain_factory 注入 Brain) + RealSay/silent + Interact). 有 SEED_TTS_API_KEY 注册 RealSay(真声), 无 key silent. 实跑(pipe): 字流边收边打 + 真声出声 + quit 干净退出
- [x] error 自纠(灵魂当场再看一段): ghost.ask 段循环 —— 观测有 error 当场再起一段流(新 LLM 调用, input 带首段原文与错误), 上限 MAX_RETRY=1 防死循环, 超限交回(错误留记忆下轮可见). 修掉"违规处死后直接把话筒交回用户, 没机会当场纠"的缺口. 实跑铁证(mimo-pro): 轮 1 乱写(发明 autonomous 轨 + `<tool_call>` + `<say>` 缺前缀)自纠段仍未改对 → 轮 2 看完观测完美写对 `<main:say>...</say>` 真出声, said 回执完整 —— 学习曲线: 同轮自纠给一次机会, 跨轮观测稳学会
- [x] protocol 负例补强(实测驱动): 开标签缺前缀负例(`<say>` → `<main:say>`), 唯一格式声明(不写 tool_call/function/JSON 等调用格式), create_chan 挂 main 轨澄清. mimo 实测: 补前轮 1 冷启动乱写, 补后冷启动首答直接写对 + 真出声 + 零自纠
- [x] protocol 学习原版(示例/红线/复读警告): 少量(few-shot)学样远快于规则 —— 加完整回复示例两个场景(招呼 = 动作+say, 纯聊天 = 裸文本不硬凑), 红线节集中复述易错点, "别原样复读出错标签"警告(叙述错误会再次触发解析错), "发出的是规划, 结果未抵达别抢答". mimo 实测: 纯聊天场景完美学样(句式照示例), 招呼场景语法零错误; 双通道重复(旁白+say 各说一遍)减轻未根除 —— 不再逐字复读但同义说两遍, 要不要教"一句话选一条通道"待定
- [x] mindflow 拆分(桥梁独立成 routine): ghost.ask 段循环(MAX_RETRY)退役, 何时想/想几拍归 mindflow 事件泵 —— ``user``/``error`` 两类事件, 无刺激不思考(泵挂起零开销), 聚合排队输入为一拍(input nucleus 同构), error 驱动自纠拍(再错 stale 丢弃), future 链对话屏障(拍链完成才交回用户); ghost 入口改 ``think``(text 可选 = 统一外界输入). 全套测试跟改(dispatch/brain/prompt/mutex 的 ``_turn`` 与注册), 全绿
- [x] 回声帧循环(照抄原版 need_observe): 观测非空(回执/错误都算)→ 回声帧自续一拍(不带新输入), 空拍收链, 无计数上限; ``error`` 事件与 stale 丢弃语义退役(原版佐证: dolores/_run.py L628 回声帧循环 ``while not attention.is_aborted() and need_observe()``, interpreter settles 即置 need_observe, 续帧条件是"有观测"不是"有错误"; 非致命错误同轮累积, 下个关键帧看). ghost 空观测不记记忆不广播(原版空回声不 append 同构). 全套测试跟改(假脑按调用次序索引, 凡末拍有动作的脚本补空拍收尾帧), 全绿
- [x] 拍链中断(照抄原版 attention.abort 语义): mindflow `_think` 捕获一拍抛错 —— 泵不崩(修掉"think 抛错 → run 崩 + ask future 永不 set 挂死"实害), ghost `interrupt` 留痕(reason 入记忆 `interrupted` 帧 + 广播 `interrupt` topic), brain 转译 `[interrupted] reason` 成 user 消息(下一拍可见), interact 订阅打断因, ask 屏障放行. test_dispatch 补 t_interrupt_chain(脑挂 → 放行 + 留痕 + 下一轮照常), 全绿
- [x] 多源仲裁(照抄原版 challenge 体系): Priority 7 档/ChallengeMode 4 模式/Impulse/Attention(分支序/强度衰减/同源提权/保护期/吸收全照抄), 抢占 = 在途拍 abort 协作退出走中断同一条通路留痕收链, 挂账(inject_percepts 同构)保消息不丢, 合成链消化孤儿消息, next 强制续拍; ghost `abort` request(事件置位, think 三检查点: 字流块边界/流结束/obs 竞争等待). test_dispatch 补 6 用例(Attention 单元/抢占/notify 挂账/next/首包尾包吸收/压制折进), 全绿
- [x] 一个 Ghost(脑子改注入): WiredGhost 继承层删除, `brain_factory` 类属性注入(与 Say.engine_factory 同构), brain.py 只剩 Brain 组件(不继承 Ghost); 测试假脑全改普通类(FakeBrain/StreamBrain/PacedBrain), 装配处注入 + finally 复位. 全套测试跟改, 全绿
- [x] 胶合层修复 + ask 对齐原版(fire-and-forget): 修 v2 自创缝合处的真 bug —— ① ask future 链整体删除, 改"入场即回 verdict"(原版信号进 nucleus 队列异步消费, 无调用方屏障; 顺带灭掉静默态 aside future 孤儿 bug, 并让终端中断可达), 新增 idle 静默探针(无链/队空/无挂账)补测试与收摊的同步缺口; ② 首包不与异包折叠(修混队聚合挂死: complete=all + id 取最新 → 链错戴首包 id 等不来的尾包), 首包开链时同 id 尾包当场吸收、完整邻居挂账; ③ 吸收提到保护期之前(修挂起链永挂: 原版靠 nucleus suppress 缓冲重试兜底, 无 nucleus 的替身); ④ 未完输入落败不挂账(尾包带全量, 不重复不丢); ⑤ interrupt 留痕请求失败只记日志(泵不崩优先); ⑥ 挂账上限 100 丢最旧(DoS 闸). test_dispatch 补 4 回归用例(静默 aside/混队两序/保护期吸收/未完不重复), 全套跟改
- [x] 记忆有界(滑窗): 帧窗(Moments 按帧计, 原版 max_moments_size=100 同参), 出窗即忘; recall 返回回合制条目 + 帧视图 + transients + dropped/epochs; max_moments 入参(Moss 透传). 语义照抄原版(含"不修齐回合边界"). test_dispatch t_memory_window, 全绿
- [x] **Moment 帧结构完全对齐**(原版 ``blueprint/moment.py 同构, 用户拍板"完全对齐"): 新建 ``moment.py``(Echoes 回声: 回执+stop_reason+need_observe / Moment 关键帧: previous 缝合缝+percepts 按源+dynamic_context 瞬态+logos / Moments 观察器: 帧链滑窗+pending 回声+瞬态槽+epoch), ghost 每拍开帧(缝上拍回声)+logos 落帧+**中断拍反幻觉缝合**(部分执行 logos 入史, 原版 executed_logos 同构), mindflow percepts 按源透传(挂账按源记, _merge_inject 同源续接), brain 转译非 user 源 ``[源]`` 前缀; 删除平铺版 memory.py. 瞬态/Epoch(前两条决策)平移进帧结构. test_dispatch t_memory_window/t_transient_state/t_epoch 适配帧语义, 全绿
- [x] **八核审计 + 三核补齐**(用户拍板"对齐原版"): 审计表定版(input/notify/aside/cell_event/broadcast 参数组合已覆盖, interrupt/knock/command 三缺口), 同轮补齐 —— 纯打断(ask interrupt=True: 停而不答空拍留痕 + 胜利侧冷静期 0.5s)、knock(mode=knock: losable 失败即丢)、反射弧(command 请求: logos 直投不调脑, last-wins, FATAL=fatal_command); 可扩展机制定版"参数面即扩展面"(三级递进, 见决策条); **打断收尾**(修既有隐患: 在途拍被抢占 shell 从未收尾, 未闭合标签吞新拍的字 —— 原版 thinking 退出即 shell.clear 同构): ghost 异常路径补发 abort 收尾令(解析器重开/处死未决流/轨打断: 未 start 丢弃+在跑送 abort), 拍号门禁(旧拍回声只入史不串台, 空观测照样唤醒), shell _finish 双形态(close/abort), channel 加 abort 控制面; interact 加 /stop 与 /do 入口. test_dispatch 24→31(+t_pure_interrupt/t_interrupt_idle/t_knock/t_fold_unit/t_command_reflex/t_command_arbitration/t_abort_teardown)
- [x] **仲裁时序对齐(档2, 用户拍板"完全对齐")**: mindflow 重写为裁判循环结构 —— 检录缓冲+信号线替 Queue(原版 nuclei buffer + `_has_impulse_event`), 裁判循环(信号即醒 + 0.5s 空搏动[原版同参, suppressed 重赛时钟]), rank 群比拉取时打(`_rank_best_impulse_from_nuclei` 同构: 同源聚合/特殊核 last-wins/尾包快车道 `_AWAITED_IMPULSE_LANE`/字典序), default 输家 suppress 回缓冲重赛(`nucleus.suppress` 同构, 下届独立成链), 单源成链, ask 受理化(verdict 只剩注册级回执). 测试推演揪出两处设计级 bug 顺手修: ①尾包不可与同源邻居聚合(尾包带全量, 合并即污染 —— `_fold_source` 先提走 awaited 尾包再聚合邻居); ②抢占赢家庄"链判死-收尸"巡场窗口被 drain 后掉地上(回插检录缓冲头). test_dispatch 31→32(t_fold_unit → t_rank_unit 重写对着群比语义; 新增 t_suppress_own_term[default 输家下届独立成链]; verdict 断言类改受理级行为断言; t_pure_interrupt r2 冷静期断言改等空拍落地再敲 —— 消除与裁判循环的竞态; t_partial_mixed_queue 消除真竞态[首包若先被拉取会独立占链挂起阻塞 idle] —— 改"先成链者在途再发首尾", 交错全收敛; t_suppressed_partial_no_dup 补空回声拍脚本), 全套回归绿(dispatch 32 / ctml 9 / prompt 3 / mutex 4 / brain 4), 两份动画 verdict 叙述跟新
- [ ] L1 硬件界面(机身) — 远: 语音双通(出 RealSay / 入 asr 耳朵, 见 ASR 条); 剩机身动作映射(锥树 → 真实执行器), voice 参数接线(音色走 say 标签属性), **心跳事件源(自主存在: 定时思考/感知事件入泵, 原版 mindflow 同构 —— 不等用户也有下一拍; 泵已就绪, 只差事件源; 感知事件直接用 transient=True 的 declare —— 快照治理已就位)**, epoch 自动触发(通路已通, 真被 100 帧打穿再接心跳触发)
- [x] **入口改名 ask → declare(用户拍板)**: 档2 后 ask 名不副实 —— "问"的隐含契约是有问必答, 受理化后回执只是登记收讫(申报语义). 考据定音: 原版外界唯一业务入口是 ``add_signal``/``_on_signal``(signal.__state__ dispatched/ignored ↔ verdict accepted/ignored 逐字同构), ``add_impulse`` 是公开**调试**入口(docstring 自述"协议级集成测试用") —— declare ≈ 原版 add_signal, mossv2 单测直接构造 Impulse 才对应 add_impulse. "申报单"词汇与检录心智模型早已定版, 代码名对齐之(备选 signal 否决: 名词做方法名 + 与内部 `_has_signal` 唤醒线撞词). 全套机械替换: mindflow/interact/四套测试/两份动画, 全套回归绿
- [x] **全异步执行(用户拍板"现在就对齐")**: 长动作跨拍存活(原版 commands keep running in background 同构) —— channel 派单 seq + `_unresolved` 解决集(abort 丢弃也算解决), flush(fence ≤fence 才等才取 + grace 收束窗口 0.5s), shell close/abort 收尾走 fence+grace + 按轨后台泵追迟到回执(带旧拍号: 门禁只入史不唤醒, 下一届首帧缝合缝读到 —— ghost/mindflow 零改动), 15s 超时降级为收尾异常兜底(不再误杀长动作). 实现推演揪出两个设计级 bug: ① close 全非阻塞会丢快动作的确定性(回声拍判空提前收链) —— grace 收束窗口是原版"回声帧检查时刻容忍竞态"的工程化替身; ② abort 丢弃的派单永不到 done —— done_seq 水位改逐单解决集. 顺手修 say.py 相对层级坑(`..tts` → `...tts`, commands/ 子包挪包遗留). test_dispatch 32→34(+t_long_action_async[收束窗口内收链/跨拍存活/迟到补账/下一届首帧读到] + t_fence_no_steal[跨轨快动作回新拍自己的回声拍, 泵只追旧 fence]), 全套回归绿
- [x] **空搏动保活对齐(用户拍板"那就对齐")**: 撤 tick 空搏动重比 —— 重读原版源码发现档2 曾把 0.5s 搏动误读为"suppressed 重赛时钟", 实际原版超时是裸 continue 不打 rank(保活心跳); 原版重赛靠新事件点灯的 rank 顺带扫冷却核 + thinking 循环 1s 空缺期扫描, 执行期专注性由提权心跳(1s 刷新 strength_refreshed_at 钉平台)绝对保证. mossv2 对齐: `_arbitrate(woken=False)` 只保 `_maybe_idle`(idle 判定), 翻盘只经 declare 点灯或链终点灯(链终 `_wake_or_idle` = 空缺期扫描替身, 行为等价); 残余偏离(保护期替提权, 拍内新事件挑战保护期后仍可衰减翻盘)记入决策条待定夺. 全套回归绿
- [x] **打断原语生命周期化(用户拍板"stop就是优雅停止")**: abort 消息旁路退役 —— channel abort/kill 改 destroy/stop(含 created 悬挂泄漏修复), Say stop hook 补全 + abort 分支删除, shell 未决流处死定点走轨(kill req), 对账扣减防悬等白等. 全套回归绿
- [x] **推送制回灌 + Tracer 内嵌 shell(用户拍板"改")**: channel 退化纯调度器(完成即 trace req 报账, 结果面全删 —— req 并发 handler 不排 mailbox 序, 否则收尾等窗自锁), shell 内嵌 Tracer 总账(全轨一本+单调 idx+违规帧同入账; 内嵌非 routine, 三无判据), 帧边界 drain(close/abort 整批出账), 收束窗 0.5s 搬家保留(后被"悬等在途"撤销), 迟到 trace 即时转发带旧拍号; ghost 门禁升级"拍号匹配且拍开着"(_beat_open: 在岗等待期迟到帧就地消化 = 原版 :1148 同构, 已收/异拍只入史+pending). 退役: channel flush/fence/seq/缓冲、shell 按轨泵/_lane_seq/帧箱. 修掉已知疣(泵等 ≤fence 前缀, 同轨快单被慢单扣账). 全套回归一次全绿(dispatch 34 / ctml 9 / prompt 3 / mutex 4 / brain 4)
- [x] **悬等在途(用户拍板"先只修0.5s这个, observe 的不管")**: 撤 0.5s 收束窗 —— close 悬等全部在途落账(INFLIGHT_CAP=18s 仅防楔兜底: abort 消息排在自旋后面, 无顶楔死 mailbox), 回执必进本拍 obs, 行为不受完成时刻的时间运气影响(query_weather 超窗丢下一轮的真 bug 修掉); t_long_action_async 改写(悬等入拍+回声帧当场消化), t_fence_no_steal → t_multilane_same_beat(跨轨同拍收割); observe 旗(原版 always_observe 同构)记决策条待做. (演进: 实跑暴露"听见自己说话"反向病, 整体被"指令结果不触发"改写, 见后)
- [x] **指令结果不触发(用户拍板"obs依然记账, 只不过不驱动")**: 撤 close 悬等 + 在途计数 + INFLIGHT_CAP(指令漂着, 回执迟到入史); obs 链路不动(记账不断), mindflow 回声帧判定 `r.get('obs') or queued` → `queued` —— obs 永不驱动回声, 唯一自续 = next 插队; ghost 零改动. 测试全链跟改: dispatch 34 用例去回声帧脚本/断言重写(t_long_action_async/t_multilane_same_beat 重写为"漂着+迟到入史+下一届读到+无回声帧", t_say_and_bare_text 补"快 say 不触发", 新增 _wait_obs/_wait_log 轮询[回执入史有报账窗口, 以 obs 为准]), prompt/mutex 跟改, brain t_error_retry/t_echo_no_cap 语义反转(错误自愈挪下一轮). 全套回归绿(dispatch 34 并行 103s + ctml 9 + prompt 3 + mutex 4 + brain 4)
- [x] **交互面 PySide6 进程外挂(用户拍板"可以")**: 终端与日志混流根治. run.py kernel 开 grpc dial-in 口(ZERO_GRPC_ADDR/config 覆盖, 写 ZERO_KERNEL_ADDR), `MOSS_HEADLESS=1` 跳过 interact(终端退役); 新增 `ui.py`: UiAdapter(passive, 订阅 moss 命名空间桥接 Qt Signal + send_line 转发 declare//stop/do, interact 同契约) + HubThread(QThread 独立 asyncio loop, RoutineHub('moss-ui') 拨入, 断线重试, 收摊 cancel 收束零警告) + ChatWindow(流式字区/观测区/输入框/停止钮/状态栏). 关键依据: pubsub 订阅表在 kernel((namespace,topic)→rid)跨 hub 路由, `get_running_routines` kernel 级查询. 依赖 PySide6 6.12 入 pyproject. 冒烟三连绿: 外部 hub 拨入+订阅+declare 字流跨 hub 回流(真脑实流) / offscreen 起窗连接自关 exit 0 / AUTOTEXT 窗内自动发一句全链渲染(带 say 真语音). 测试套不受影响(run.py 纯装配, tests 自带 harness)
- [x] **interact.py 退役删除(用户拍板)**: UI 外挂冒烟三绿后终端交互面退役 —— 删 interact.py(mossv2 目录 untracked, 不可 git 恢复; 契约 ui.py 全同构在, 需时可重建), run.py 去 import/注册分支/MOSS_HEADLESS 判定(纯 server 入口, `await Event().wait()` 常驻 Ctrl+C 退), ghost.py/ui.py 悬空引用清理, 两份动画 interact 卡改"UI 外挂"(教学点不变: 感知源驱动/declare 即回). 全链冒烟绿(无终端起 moss + UI 拨入 AUTOTEXT 全链渲染) + 34 用例并行回归绿
- [x] **query_weather 假桩 + /go 手动续拍(用户需求)**: 指令结果不触发回路的显式激活路径. ① `commands/query_weather.py` 假天气桩(2s 模拟 API 延迟, 固定假数据; 真实实现换 HTTP 即可契约不变), run.py 注册 + Moss 词表 verbs=['query_weather']; ② UI `/go`(输入框)与"激活"按钮 = declare{mode:'next'} 无文本 —— **链活 = 插队下一拍(t_next_mode 路径); 链死 = 独立开回声拍**(rank 无竞争必赢, percepts 空 → think(None) 不带新输入), 开帧缝合缝缝着漂到的动作回执(pending), 模型被动读到 —— 原版 need_observe 回声帧的手动替身, **mindflow 零改动**(next 机制现成); ③ 冒烟钩子 MOSS_UI_AUTOGO_MS. 全链冒烟绿: 问天气 → 拍收回执漂着 → 25s 自动激活 → 续拍"北京今天挺不错的: 晴, 12℃, 北风 2 级, 空气质量良"(假数据全从缝合缝读到). test_dispatch 增 t_go_revive(链死/空文本/缝合缝读到回执), 35 用例并行回归绿
- [x] **UI Moments 帧链侧栏(用户需求"加一个侧栏")**: 右侧 QDockWidget 展示
- [x] **UI 重连按钮(用户需求"这样就不需要我重启ui了吧")**: moss 主进程重启
- [x] **自动重连定音(SDK 自带) + SDK 代理劫持修复(用户问"还是说自动重连更好?")**:
  考据+探针实证: **transport 断线自动重连是 SDK 内建** —— stream 断 →
  handle_peer_down(作废句柄+销毁本地实例, stop 钩子实证被调) → backoff
  0.2s×1.5 上限 5s → 重连 + 重发 catalog.push(kernel 重拉 adapter 拿新 rid,
  订阅表重建) —— moss 主进程重启后 UI **零操作自动恢复**(探针: 杀 A 0.1s 感知
  → B 起后 <1s 接上, running 表恢复满员). "重连"按钮降级为兜底(transport
  卡死时强制换拨), tooltip 改口. 可见性: adapter on_created/stop = 连接真值,
  状态栏新增"连接断开 — SDK 自动重连中(backoff ≤5s)"/恢复提示, moments 随
  恢复初拉.
  **根修一个 SDK 级 bug(探针揪出)**: shell 环境带 http(s)_proxy=127.0.0.1:
  60259 且 no_proxy 空 —— grpc insecure_channel 默认尊重 proxy env, 对
  127.0.0.1 的 CONNECT 也经代理, 代理 502 时通时不通(此前的"重连不稳定"
  全是它). 修复: grpc_client._connect_once channel option
  ('grpc.enable_http_proxy', 0) —— dial-in 是本地/内网直连语义, 永远直连.
  修后探针闭环: 10054 reset 即时感知, 重连拒绝期 10061 纯直连, 502 绝迹.
  35 用例回归绿
  (kernel 全新, rid 全换)后 UI 不重启原地换拨 —— ChatWindow._reconnect: 停旧
  hub 线程(复用已验证的干净收摊: 销毁本地 adapter/停 transport/loop 退场;
  wait 3000 超时 terminate 兜底)→ **等旧线程退净**再起新线程重拨(顺序硬约束:
  旧线程退出清模块级 _loop/_adapter, 抢跑竞态会抹掉新线程刚设的全局) →
  _adapter=None 过渡守卫(派活提示未连接); 新线程沿用 connect 失败 3s 重试
  循环(B 未起也不慌). 冒烟: 连 A 发话 → 杀 A(SDK peer kernel down 自动作废
  句柄) → 同端口起 B → 20s 自动重连一次拨上 → 侧栏如实归零(B 全新 kernel,
  帧0) → 再发话 B 真腔回话("上次问的城市没有记录了"——恰好证明换的是新进程)
  全链通. 钩子 MOSS_UI_AUTORECONNECT_MS/AUTOTEXT2(_MS)
  ghost 记忆的只读快照. ① ghost recall 观测面扩两键(加法无破坏): `pending`
  (漂到未归账回执 + need_observe 火种旗, moment.pending_view)与 `recap`(epoch
  前情提要); ② ui.py: UiAdapter.fetch_moments(ghost 按名查 rid → recall → 瘦身
  载荷 emit, UI 只被 req 读不碰记忆) + MomentsPanel 渲染(recap[紫]→pending[珊瑚,
  侧栏的"进行时", obs-旗亮时提示 /go 可消化]→帧链[新→旧: 缝合缝/输入/logos 一帧
  一卡]→瞬态槽[灰]) + 防抖拉取(连接/obs/打断/发送四处点灯, 250ms 合一次) + 刷新钮.
  排列即语义: pending 永远压在帧链上面(等下一帧缝走). ③ t_go_revive 补 recall
  新键断言(漂到回执在 pending + 火种旗亮 + recap 空). 冒烟: 连接初拉帧0 → 发话
  开帧1 → 天气回执漂进 pending(obs-旗=True) → /go 开帧2 缝走清零, 全链刷新四拍
  各就位; 35 用例并行回归绿

- [x] **UI 渲染"我说的话"(用户需求"应该可以渲染我说的话吧, 可以流式渲染吗")**:
  耳朵加一根**旁观针** → `moss/heard` 广播 → UI 整行改写(流式)。
  ① `listen/controller.py`: `ListenerController(witness=...)`, 同步针
  `(本段全文, 定稿?)`; 三处调用 —— `_on_partial`/`_on_clause` **先于** `_feed_judge`
  (判停可能同步 commit, 顺序反了会先收定稿再收"还在说", 把定稿行重新激活成半截话),
  `_commit` 在 close 之后、deliver 之前(交付要等 declare 回执, UI 不该陪着等);
  `send_now` 的留存分支补一次 final(留存礼仪没有判停那次 commit, 否则行永远停在
  partial). 针内异常吞掉 —— 渲染坏了不能拖垮耳朵.
  ② `asr/routine.py`: `_witness` 投 task 广播 `moss/heard {kind: partial|final, text,
  source}`(fire-and-forget, 不等回执; `partial` 是**本段全文**不是增量, 订阅方整行
  替换即流式); stop 取消在途.
  ③ `ui.py`: Bridge 加 `heard` + UiAdapter 订阅 `moss/heard` + `ChatWindow._on_heard`
  记 QTextBlock **就地改写**(不追加); 新开行时若它正在流式输出先断行 —— 我开口=打断,
  我的话不该长在它半句话里; 打字与语音共用 `你: ` 前缀.
  验证: `t_listen_witness`(partial 全文 / final 一次且先于交付 / observer 强发送补定稿)
  + 新增 `tests/test_ui.py` 3 例离屏(流式整行替换不刷行 / barge-in 断行 / 前缀一致);
  真链路播报 → `[heard] partial ×6 → final` → `[text]` 模型流式回复. 45 例并行绿.

- **打断并行化（2026-10-17）**：`shell._finish` 的 abort 分支从逐轨
  `await req(rid, 'abort')` 改 `asyncio.gather(..., return_exceptions=True)`
  —— 各轨是独立实例（各自 mailbox / 队列 / 在跑 handle），请求互不依赖；
  N 轨往返从 sum 摊成 max，且串行时一轨卡住（req 默认 30s 超时）会把后面
  几轨连同整条收尾一起拖住。失败帧按完成序入账（跨轨本无顺序语义，
  谁失败谁留痕），`_touched` 的"保序"注释随之改口。channel 侧 `_abort_entry`
  内 victims 保持串行：`destroy(fire=True)` 是发消息即返回
  （handle.py:198-202），没有 RTT 放大，并行只增复杂度不增收益。
  实测（临时脚本给每条轨 abort handler 注入 0.3s 延迟）：三轨 abort 完成
  离散度 0ms、单条 308ms，串行三条应 ~900ms；脚本跑完即删。
  新增 t_abort_multilane（main: slow 在跑 + y 排队 / a: x / b: z 同拍在途 →
  排队的 destroy 无账、在跑的 stop 收底照常报账、三轨回执全落）；
  36 用例并行回归 ALL PASS。

- **错误面定版：只报"异常错误"，不搬分级（2026-10-17）**：原版
  `CommandErrorCode`（2xx 调度 / 3xx 记账不点灯 / ≥400 强制观测）语义考据
  已完整存档，但**暂不移植** —— 用户拍板"现在搬过来为时尚早，不是重点，
  只需要知道它是异常错误、报给 agent 就够了"。现状已满足最小语义：
  channel 动作失败 `_trace({'kind':'error','verb','text':str(exc)})` → 账本 →
  drain 回灌 → brain `_frame_line` 渲染成 `error(verb): 错误串`（尖括号换全角
  防回声复读）。协议违规（坏标签/未声明 verb/未建轨）走 `_frame_error` 同格式。
  **挂账缝隙**：按"指令结果不触发"定版，error 帧同样只入账不驱动 —— 模型要等
  下一届（/go 或新输入）才看见失败。真要"错误立刻回调模型"，挂在 observe 旗
  落地时一并拍板（原版对应条款：≥400 强制点灯）。**不做的事**：不引入错误码
  枚举、不做 2xx/3xx 分支、不让错误自续拍。

- **错误是回声的一种（2026-10-17，用户"Go 语言看待 error 的视角"）**：
  error 不是另一条控制流，是结果的一种 —— `channel` 报账帧与成功帧**同构**
  （`verb`/`chan`/`rid`，只换载荷键 `result` ↔ `text`），同一本账、同一次
  drain、同一个 `Moment.previous` 缝合缝，`moment.py` 对帧不做任何 kind 判别
  （帧是不透明字典）。呈现层唯一的区别是 brain 渲染成 `error(verb): ...`
  （让模型知道失败，防幻觉成功），不改变它的回声身份。
  由此 observe 落地时错误**不需要特例**：声明集合按 `rid` 对账，报错与成功
  一样把该 rid 从集合里划掉，集合清空即点灯（echo）—— 不需要抄原版
  "≥400 强制点灯"（未声明也激活）那种父权例外。
  改动：`channel` 两条 trace 补 `rid`（`handle.id`，`t_verb_failure_isolated`
  断言两种帧字段同构、两把 rid 各不相同）。

- **回声与手动激活同灯（2026-10-17，用户"统一走跟手动激活同一条路径"）**：
  手动 next 与观测回声在原版**本来就是同一盏灯** —— next 挑战失败
  `verdict='queued'` 时正是 `add_echoes([], need_observe=True)`
  （`_mindflow.py:714-717`），观测完成点的是同一个旗
  （`mindflow_in_shell.py:186-188`），两者最终由 attention 内同一个
  `while need_observe()` 消费（`:1148`）。所以"统一"的落点是**灯**，
  不是 RPC：回声若绕一趟 `declare{mode:'next'}`，多一圈 RPC 且链可能已收尾，
  反而丢掉"免擂台续拍"（原版回声不重建 attention、不重排名）。
  落地：`_queued_beat: bool` → `_need_beat` + `_request_beat(reason)`
  单一收口，两个点灯人 —— `_dispatch` 的 next/queued 分支与 `_think`
  里灵魂报 `echo`（声明过的观测清空）。灯幂等（一拍内点几次只续一拍）；
  被抢占打断在续拍前一拍已收时，灯跟着落成下一届首拍（原版旗挂 observer，
  attention 死而旗活）。新增 t_echo_lamp（冒充灵魂报 echo：同链续一帧、
  只多一拍、带缝合缝）；next/go/echo 三例绿。

- **动作 meta 的 `always_observe`(2026-10-18 用户"routine meta 里加 need_observe?")**: 原版就是
  meta 字段 —— `CommandMeta.always_observe`(command.py:315), 声明入口 `@chan.build.command(
  always_observe=True)`(channel_builder:362/:587), 合并语义只有一行:
  `task_result.observe = task_result.observe or self.meta.always_observe`(:1667).
  mossv2 落位: **声明跟着动作走** —— 动作 routine 的 `meta` ClassVar 写 `always_observe: True`,
  装配方零配置; Shell `on_created` 装配期扫一次 catalog(`ctx.get_routines()` 返回 name+meta)把
  词表里自报的动作收成 `_always_observe`, 查询失败降级空集(装配不因它失败); dispatch 处
  `observe = observe="true" 声明 or verb in _always_observe` —— 与 :1667 的 or 同构.
  **三层债源合流**: 错误帧(运行时强制) / meta 声明(出厂, 查询类天生要结果) / `observe="true"`
  (调用期, 这一次特别要) —— 谁给了都算债, 全落到帧上的 `owed`. `query_weather` 是第一个受益者:
  模型调它不必写 wait, 结果自己回到眼前. 不做: 运行期改 always_observe(registry 只动词表).
  **默认不看不写死**: meta 没有该键 = 无债(只记账, 下一届缝合缝读到) —— 白名单语义, 因为
  多数动作是 fire-and-forget(say/挥手动脚), 全员观测等于每步都多想一拍.

- **create_chan 也要 `always_observe`(2026-10-18 用户"create_chan 也需要吧?")**: 要 —— 且这条漏
  是结构性的, 不是偏好. 开轨是**拓扑变更**, 回执(`{chan, lanes}`)是"新轨可用"的唯一凭据; 而轨表
  闸门在 dispatch 时同步查 `_lanes`, 开轨本身跨拍(动作在轨上排队, 回执不可能落在同一拍), 所以
  **没有回声拍就没有"下一拍能用新轨"**—— 模型要么等用户再开口, 要么撞 `unknown lane` 报错.
  更糟的是单向盲区: **失败会点灯(错误自带债)、成功不点灯**, 模型只知道"炸了", 永远不知道"成了".
  落位: `CreateChan.meta['always_observe'] = True`(与查询类同一条路, 声明跟着动作走, 不给 shell
  加特例表); prompt 改写(回执会立刻叫一拍, 那一拍起新轨可用 / "动作自带声明"涵盖开轨). 测试:
  t_chan_lifecycle 升级 —— 轮 2 由两拍组成(主拍 + 回声), 断言回声拍真读到 create_chan 回执
  (不是白开), 轮 3 的并行断言照旧. 已知边界: 扫描是**装配期快照** —— 运行期 `registry reg` 挂进来
  的  动作不在 `_always_observe` 里(与"不做运行期改声明"同一刀).

- **ASR 接入口: 耳朵 = 自实现器官 + 原版礼仪层(2026-10-18 四连问: "asr要接一下" →
  "这个 asr 不是我想要的, 之前的版本是自己实现基于 doubao 的" → "asrserver-rs 这个是 rs 实现"
  → "moss原版是怎么处理asr的, 他貌似有几种策略" → "可以都复刻过来吗")**:
  **器官**(`asr/`): 本机麦克风 + 豆包(火山 SAUC)流式识别, 协议对照
  `E:////code////pyfiles////sherpa-onnx////services////asrserver-rs////src////volc.rs` 逐字节实现(`sauc.py`)。
  三个协议要点(踩过才知道): ① Agent Plan 专属 Key 只认 `/api/v3/plan/sauc/bigmodel_async`
  (与 seed-tts 同一把 key), 普通 `sauc/bigmodel_async` + `volc.seedasr.sauc.duration` 对这把
  key 一律 401; ② 参数同 volc.rs: `enable_nonstream=False`(定稿靠 utterance.definite)、
  `enable_ddc=True`、`end_window_size=800`、`result_type=full`, 200ms 一包; ③ 鉴权头
  `X-Api-Key` + `X-Api-Resource-Id` + `X-Api-Request-Id`/`Connect-Id`/`Sequence:-1`(旧账号可用
  `X-Api-App-Key`+`X-Api-Access-Key`)。采集 sounddevice(16k 直采被拒则退原生率、回调里
  numpy 降采样); 断线退避 0.5→8s; 鉴权连续 3 次 401/403 停机待命; 句界换流复位上下文。
  **半双工**: `say` 出声期 publish `pubsub 'output'`, 耳朵丢麦克风块(0.35s hangover) ——
  本地喇叭+本地麦无 AEC, 不丢就是自听回灌; 代价是说话期不能 barge-in。
  **丢麦 ≠ 断流(2026-10-18 用户日志 `45000081` 修)**: SAUC 约 8.5s 收不到音频包就判
  "Timed out waiting for the next audio packet" 掐会话 —— 静音期停发会让会话每 8s 死一次
  (能重连, 但白丢上下文). 发送泵改为**队列空即补 200ms 静音包**, 线上永远 200ms 一包
  (静音包与真实块用同一 seq 序列, 有块时绝不拿静音顶替); 另加静音兜底
  `_MUTE_MAX_S=120s`(闸门 stop 帧若丢, 强制放麦) + `say` 收尾的 `_tell_output(False)`
  走 `shield`(force stop 取消主任务时也要把闸门放下).
  **礼仪层**(`listen/`, 原版 `host/listener` 的移植): 原版不是模式枚举, 是**三个正交旋钮的积**
  (etiquette.py 原话 "an interaction style is a point in their product space — not a code branch"):
  `onset`(emit/interrupt/priority) × `stop`(silence/keywords/classifier/detect) ×
  `deliver`(emit/history/interrupt/priority/mode/low_confidence)。11 个开箱坐标全搬:
  once/always/aside/notify/scribe/observer/keyword_end/scored/murmur/cue/knock。
  - `events.py`: text axis 四相位(FIRST/PARTIAL/CLAUSE/TAIL) + Clause/Word/Segment 契约
    (去掉 audio 存档轴); `Priority`/`ChallengeMode` 取值与原版逐值相同, 所以礼仪字段可原样喂 declare。
  - `stop_judge.py`: 四条判停路(静音展期/关键词/分类器打分≥阈值/能量→knock)先到先 commit,
    单 `_committed` 闩锁 + 置位与 commit 之间无 await; classifier 有 0.3s 防抖(被下一句取代不花调用);
    与 `deliver` 的 `score` 观测同构。**唯一改写**: caller 从 `MossLLMCaller.run_messages(list[Message])`
    收窄成 `async (prompt: str) -> str`, 不依赖 ghoshell 消息类型。
  - `segment_buffer.py`: current 增长全文 + recent 环形 + forgotten 计数 + drain(输入法强发送),
    生命周期边界 = segment 签发(不是 ASR 的 VAD 定稿)。
  - `controller.py`: **只搬策略, 装线换成三根针** —— `emit(**fields)`→declare、`feed(ev)`←耳朵、
    `ScoreCaller`(可选)。段由本层切(豆包 utterance 是"句", 一轮应由礼仪判停决定), TAIL 是 commit
    合成事件; 起话 complete=False 占坑 → 交付 complete=True **同 id 吸收**填坑, 一句话只算一轮。
    `once`(silence=0)/`observer`(emit=False 只留存)/`scored`(分类器) 全部可切; req 面:
    `etiquette`(查/切) / `upsert_etiquette`(整份 JSON) / `transcript`(拉读) / `send_now`(强发送)。
  **运行期可编程**: 模型/调用方随时 `req asr etiquette name=observer`, 与原版 listener channel 的
  `set_etiquette_spec`/`activate`/`get_transcript`/`send_now` 同构(只是走 routine req 而非 channel)。
  - **onset 四规矩**(原版 `ListenerNucleus` 除"两包映射"之外的专属设计, 全部只作用于**起话包**,
    交付包一律豁免 —— 内容不能丢): ① **首包提权** `first_strength=150`(同级强度仲裁用, 与
    `source_escalation` ×1.1 配合; 调用方给 100 也由核钉回 150); ② **上台降级**
    `on_attended` —— 抢到 attention 立刻回 `INFO` + `normal_strength=100`, 否则占坑包以 150
    钉在台上, 同源下一个包再也进不来(多轮对话退化成"抢到就独占"); ③ **两层冷却**
    (输了 `onset_suppress_seconds=0.5s` 大冷却 / 赢了 `attended_seconds=0.1s` 小冷却, 防"抢到
    后立刻被自己下一个包抢"), 冷却期内起话包在 `govern` 里丢、交付包照发 —— 与
    `Nucleus.suppress_seconds`(该源整体降频)是两回事; ④ **强度 TTL**
    `strength_decay_seconds=120s`(通用默认 20s)。落点: 核的
    `govern`/`on_attended`/`on_suppress` 对应原版 `_on_onset`/`_on_deliver`/`attended`/`suppress`;
    `req nuclei name=asr` 的 `onset` 段自解释这些参数与当前冷却状态。
  **已对齐(2026-10-18, 修掉真分歧)**: 原版 `interrupt`(`shell.clear()` 停身体) 与
  `effort`(答不答, `thinking_effort`) 是**两轴正交**; mossv2 曾把两者压进 `interrupt`
  一个开关("纯打断: 停而不答"), 于是 `always` 的 onset(interrupt=True)走空拍而不占坑,
  `t_onset_absorb` 只能靠置 `onset.interrupt=False` 绕过 —— 测试绕了, 真链路没人绕
  (自听验证: 起话/交付都 accepted 但不出声)。修法: `Impulse` 新增 `effort`
  ('normal'/'none'), `record_only` 判据从 `impulse.interrupt` 改为 `impulse.effort == 'none'`;
  `interrupt` 收窄为"停身体"(抢占 abort), 裸写 `interrupt=True` 仍默认补 `effort='none'`
  (= 原版 interrupt primitive 全组合, 向后兼容 `/stop`、`t_pure_interrupt`), listener
  的起话/交付包显式传 `effort='normal'` = barge-in(停旧回复但正常回答)。附带修两处:
  ① `_challenge_incumbent` 把同 id 吸收提到 FATAL/interrupt 之前(原版 `AbsAttention.challenge`
  第一判据就是 absorb —— 否则带 interrupt 的交付包会被"FATAL 必赢"挤成抢占, 一句话断两轮);
  ② 打断冷静期只挂 `effort='none'`(barge-in 起话赢了不该堵下一句起话); ③ asr 的 `_emit`
  字段白名单补 `effort`(否则被 declare 按 primitive 补成 none)。
  **partial 跨句污染(2026-10-18 真链路暴露)**: 服务端 `result.text` 是**整条流**的全量累积
  (含已定稿的历史分句), 而 controller 的 `_on_partial` 是全文替换 —— 上一句会随 partial
  混进下一段的 `_text`, 交付变成"上一句 + 这一句"(实测第二句交付
  `'你好，请简单介绍一下你自己。那你能做什么呢那你能做什么呢？'`)。修法: ear 记 `absorbed`
  (流内已定稿分句拼接), partial 输出前裁掉该前缀; `definite` 游标 `emitted` 不动(服务端
  utterances 数组按流累积, 只增不滚)。`enable_ddc` 无关(开/关实测同样累积, 已回 True)。
  **未搬**: idle_timeout→knock 门铃降级(要 Ear 暴露 rms_db 能量检测); 故障有界自愈 3 次(Ear 已有
  重连, 控制器层未做 degraded 信号); `expect`(语音输入超时提醒); 人类锁 `_human_paused`;
  ClauseTopic/AudioSampleTopic 装线(宿主可观测面, 非策略)。
  验证: `tests/test_listen.py` 4 例(礼仪空间/判停三路/留存槽/控制器 onset→deliver 同 id + observer
  留存 + 切礼仪) + `t_asr_intake`(once 下定稿交付/半双工/诊断) + `t_onset_absorb`(起话占坑→交付填坑,
  历史只一条) + `t_onset_nucleus`(首包提权/上台降级/两层冷却只拦起话/120s TTL)
  + `t_barge_in`(interrupt 停身体但 effort=normal 照常回答, 与 `t_pure_interrupt` 对照)
  + `t_sauc_protocol` + `t_ear_keepalive`(静音期补 200ms 静音包保会话, 有块时不顶替);
  真链路自听: TTS 播"今天天气怎么样" → partial×3 → utterance 定稿; 静音 15s 实测会话不死
  (修前 9.5s 被 `45000081` 掐断)。
  总线路径已撤: `bus_listener` 不再进 `run.py`(耳朵自带采集)。测试: 46 例并行 ALL PASS。

- **模式控制 always / ptt(2026-10-19 用户"需要加asr模式控制 routine, 可以always, 也可以ptt
  按下/松开指定按钮 进行asr" → "pynput去做监听?" → "不需要兜底,用不掉直接报错结束不就行了")**:
  **`asr_mode` 是普通标签指令**(`<asr_mode mode="ptt" button="f9"/>`), 一个标签一个实例, 但它
  自己**不存状态** —— 把 mode/button 写给耳朵(`req asr mode`), 状态活在耳朵里, 实例退出后模式
  不变. 这就是"加个 routine 管模式"与"模式是耳朵的属性"之间不需要二选一的落法.
  **门在耳朵侧**(策略不散在调用方): `_listening()` = `mode=='always'` 或 `'ptt'` 且按住;
  `partial`/`utterance` 一律过门, 不在听就丢(踩坑: 原来是 `if partial … elif not listening …`
  的 elif 链, partial 永远轮不到门, 没按住也发出起话包 —— 断言靠日志 `'不该听到' accepted` 抓到).
  **松手交付**: `release` → `_flush()` → `controller.send_now()`, 前面留 0.12s 排空窗口
  (队列里已采到但没消费的块), 窗口内 `_flushing` 置位让门保持"在听", 否则最后半句被自己丢掉.
  `mode` 查询**必须只读** —— 早版把 `_ptt_down` 冲掉并把麦关掉, 键盘 1s 一查就把 ptt 按死了.
  **`key_listen` 是被动常驻**(`is_passive`): 键名不在这里配, 每 `refresh` 秒向耳朵问当前
  mode/button(唯一真理源), 只有 ptt 才建监听、切回 always 立刻 `stop`(不白占全局钩子).
  后端**只有 pynput 一个**(`pynput>=1.8.2` 已在依赖里), 事件式、跨平台, 回调在自己线程里
  `call_soon_threadsafe` 转回循环, 只做一件事: `req asr press|release`.
  **没有兜底(用户拍板)**: 原来那套 Windows `GetAsyncKeyState` 轮询兜底整段删掉 —— 导入失败
  `RuntimeError`、键名不认识 watcher 构造即 `KeyError`、平台没钩子 start 即抛, 统一由刷新任务
  记成致命错、`run` 收尾后抛出, **实例直接结束**. 理由: 一个躺着看起来在跑却永远收不到按键的
  监听器, 比明确报错退出难查得多; always 模式本就不依赖它, ptt 起不来就该看见.
  保留的注入点只有两个(`watcher_factory` / `backend_check`), 给测试替身用 —— 与"降级兜底"不同:
  替身是**替换**, 兜底是**悄悄换一条更差的路继续跑**.
  验证: `t_asr_mode`(标签切模式 → 耳朵状态 + 回执 `{mode,button,listening}`)、
  `t_asr_ptt`(不按不开口 / 按住才收 / 松手立刻交付 / 尾巴不重复)、
  `t_key_ptt`(键边沿 → press/release, 切回 always 停钩子)、`tests/test_keys.py` 4 例
  (名字映射 / 无兜底契约 / pynput 缺失即抛 / 坏键名即抛). 测试 71 → **74 例并行 ALL PASS**.

- **提示词跟能力在一起(2026-10-19 用户"能力提示词应该跟能力在一起吧, 没有注入能力的话,
  相关提示词自然不应该出现")**: 原 `prompt/protocol.md` 把 `say` / 四容器 / `sleep` /
  `noop` / `clear` / `interrupt` / `code` / `create_chan` 全部硬编码讲了一遍 —— 词表里
  没有这些动作时也照讲; 全部提示词里只有 capability 段是跟词表走的. 改法两条腿:
  ① 新增 **`meta['usage']`**(多行示例/陷阱), capability 渲染时缩进跟在动作行下面, 且
  **只渲染当前词表里的动作**; ② `protocol.md` 瘦身(167 → 80 行), 只留与具体能力无关的
  东西: 标签形态(开闭标签/轨道前缀/属性引号)、编排机制(容器结构用"容器名/动作名"占位、
  跨轨栅栏)、观测回灌(`wait="observed"`)、错误块、时机、红线 —— **具体动作一个字不提**.
  内容搬家(不是新写, 是把重复的搬回它主人那儿): say 的闭标签陷阱/边到边收 →
  `say.meta['usage']`; code 整段(示例/走法/句柄/跨格变量) → `code.meta['usage']`;
  create_chan 的开轨回执 → `create_chan.meta['usage']`; 容器族共享结构(body 里放指令/
  占道/timeout/跨轨栅栏) → `container._CONTAINER_USAGE`(四个容器 meta 指向同一份).
  **渲染去重**: 同一段 usage 只印一次(按文本判重), 否则容器族会重复四遍.
  顺手修: 清单里 `__scope__` 显示成 `_`(标签面写法, 否则模型想不到 `<_>`); 四个容器
  description 各自精确化(原来 all 与 any 共用"全部/任一"一句).
  验证: `test_prompt` 3 → 4 例(+`t_capability_follows_verbs`: 进词表才有 usage / 不在
  词表行与用法都不在 / 容器共享段只印一次); dispatch **74 全绿**; test_mutex(含
  `t_capability_grouped`) / test_body / test_ctml / test_listen 全绿.
  **键名更正(2026-10-20 用户"meta里面可以 example去说明吧, usage这个词不太通用吧?")**:
  上面写的 `meta['usage']` 现在叫 **`meta['example']`**(常量 `_CONTAINER_USAGE` →
  `_CONTAINER_EXAMPLE`), 渲染、缩进、按文本去重全部不变 —— 只是作者侧的词更直白:
  这个字段装的是"这个动作怎么用"的多行示例(可含陷阱). 历史段落保留原名, 以这条为准.

- **收摊零警告: cancel 必须收尸(2026-10-18 用户贴 "Task was destroyed but it is pending! / Event.wait")**:
  病根是 **cancel() 后不 await** —— 取消只是把 CancelledError 排进任务, 真正结束要
  再走一轮 loop; loop 若先关(kernel 关停/线程收摊/GC), 任务就带着 pending 状态被销毁.
  `Event.wait()` 是最常见的受害者(恰好是"等一个事件"的等待任务形态). 三处补齐:
  ① **ghost.think 观测等待**: `obs_wait/abort_wait` 取消后 `gather(return_exceptions=True)`
  —— 每拍一对, 不清就是每拍留两颗尾巴; ② **ghost 火忘补拍**: `_knock()` 任务进
  `_bg_tasks`, `stop()` 里取消+收尸; ③ **framework gRPC peer 收尾** (`grpc_server._on_stream`):
  原来 writer/worker 各 wait_for 2s、超时 cancel 不 await —— 改为 shield+wait_for 给
  优雅窗, 再统一 cancel + gather; `reject_t`(coro 正是 Event.wait)也在其中.
  **UI 侧**(ui.py HubThread): `_serve` 收摊两步加 3s/4s 上界(超时会触发主线程
  `terminate()` —— 线程被硬杀才是真正把任务留在 pending 的路径); `run()` finally 里
  再扫一遍 `all_tasks` 取消收尸后才 `loop.close()`; `halt()` 早于 loop 就绪时记账
  (`_halt_wanted`), 起来即关, 不再"关不掉只能 terminate". run.py 关停加 10s 上界.
  实跑验证: 真脑一拍 + 客户端断开 / 打断抢占 / HubThread 正常与抢跑关停, 全程无
  destroyed 警告; 39 用例并行回归 ALL PASS.

- **统一指令形态: 容器不再走 magic(2026-10-19 用户"就是要这样 重构吧 保持优雅和洁僻")**:
  病根是两套形态并存 —— 普通指令"开标签即入轨", `scope` 却是解析器认得的保留字
  且没有实例(靠 shell 记账). 用户拍板: **一个标签 = 一个实例 = 一个 body 解释器**,
  实例层是树、执行层(轨道)是平的; `all`/`any`/`_` 只是注册在词表里的**容器命令**,
  和 `say` 平起平坐, 解析器不认识它们. 落地: ① `body.py` 深度 1 扫描(`Text`/
  `Malformed`/`ChildOpen`/`ChildBody`/`ChildClose`, 视野只一层, 子 body 里一切原样
  转发, 闭合按**指令名**配对使 `</say>` 能关 `<main:say>`); ② `container.py`
  (`Container` + `All`/`AnyOf`/`Flow`, 量词照抄原版 `wait_close`: all 等全部 /
  any 任一完成即真取消其余 / `_` 只等本 lane 空则容错等全部); ③ shell 只剩一个
  **投递服务 `route`**(词表 + lane 闸门 + 编排参数注入, root 与容器共用),
  `_ALIAS={'__scope__':'_'}` 属名字层不是解析器魔法; ④ **占道不需要新机制** ——
  容器实例本身排在 lane 队列里, `run` 等子完成才返回, 后面同轨动作自然等它;
  但容器必须**延迟入队**(收完 body 后由**容器自己** `release`, 不是 shell 发完
  eof 就放 —— 否则它会抢在自己的子/展开之前入队, 自己等自己) → 死锁; ⑤ 回执**谁投递谁收**(receive 走 `req` 不走 `send`: 请求并发处理,
  否则被收尾令堵在 mailbox 后面变成迟到帧), 容器收齐向上汇总一层, 账本仍 root
  一本. 删除 `ctml.py`(全局栈 + 保留字)及其单测.
  **错误债顺带收口**: `_frame_error` 原来自建一条入账小路并手写 `owed`, 违反
  "产戳唯一产地 `_record_trace`" —— 改为只造 `kind='error'` 交报账, 债由报账处
  判(违规=契约未履行=自带债). 行为变化: 违规**当拍续一拍矫正**(原 `t_error_retry`
  /`t_echo_no_cap` 断言的"违规单拍收链"是 10-16 旧语义, 已按定版重写为
  `t_error_retry`(违规当拍矫正) + `t_error_echo_once`(违规只买一次回声, 矫正即
  收链)); 附带修掉一个潜在洞: 收尾后才报的错不再堆成无人取的死帧, 按迟到回执
  即时转发. 用例: 46 → **49**(+`t_container_all`/`t_container_flow`/`t_container_any`)
  + `test_body` 8 例; 全套并行 ALL PASS.
  **容器收尾竞态(当日补)**: `t_container_all` 偶发 `reply failed: routine all stopped
  before replying` —— 完成事件在回执处理器里置位, `run` 就被立刻唤醒并收摊, 抢在
  该请求应答之前 → 帧已送到却报丢. 改为**处理器只交货**(先向上转交, 再报"有一批
  到货"), 完成由 `run` 在 `_settle` 里从到货清单标(`_mark`/`_enough`, 量词判据共用
  一份); `stop` 置 `_stopped` 放开等待并 `everyone=True` 收全部子(不再受 flow 过滤).
  这条是"谁投递谁收回执"的配套纪律, 已写进契约文档.

- **原版标准原语全表(2026-10-19 用户"那 5 个加上也很简单")**: 对照
  `D:/MOSShell` 的 `inject_system_primitives`(标准 5 / 实验 6), 补齐标准档 ——
  全部是普通指令, 没有一个新机制: `sleep`(普通动作; 原版 `chan` 参数想表达的
  "在哪条轨阻塞"由标签第一槽给出) / `noop`(普通动作; 让"选择沉默"在账面上与
  "没有输出"可区分) / `loop`(容器) / `clear` + `interrupt`(控制).
  **`call_soon` 是调度属性, 不是旁路**: 清轨指令若排在排程里等前面跑完就什么也
  清不掉 —— 原版用 `PyCommand(call_soon=True)` 表达, 这里照抄成指令 meta 声明;
  channel 收到不入队直接起跑, 入口/生命周期/回执链路全同. 抢占实例不占
  `_current_*`(串行槽位), 所以清道夫不清自己.
  **loop 用展开而非重入**: 容器一旦被 worker 取到就占住这条串行队列, 此刻再投
  同 lane 的子会自己等自己; 原版 loop 本就不是 channel 任务而是命令栈展开器
  (先收全 body 再展开), 故照做 —— 在 eof 处理里按轮重放 body 原文, 全部轮次的
  子先于 loop 入队. 连带修出更根本的时序: **容器收完 body 自己 `release`**
  (shell 只发 eof) —— 否则"发完 eof 就 release"会让容器抢在展开之前入队, 同一个
  死锁. 边界: 跨轨轮次无屏障(单轨完全等价, 队列本身串行).
  **`timeout` 属性**从闭标签起算(容器排队等前序的时间不算"子等了多久"), 到时
  取消剩余, 回执 `timed_out` 自带债. **第四债源**: 动作结果自报
  `{'observe': True}`(原版 `CommandTaskResult(observe=True)`), loop 收尾与超时
  用它, 债仍在 `_record_trace` 一处判.
  用例: 49 → **56**(+`t_sleep_occupies_lane`/`t_noop_is_an_action`/`t_loop_times`/
  `t_loop_stops_on_error`/`t_container_timeout`/`t_clear_drops_queue`/`t_interrupt_all`).

- **跨 lane 栅栏 + 闭标签握手(2026-10-19 用户"比如 b 轨道要等 a 的做完再开始")**:
  屏障写法 = 容器锚在**要等的 lane**、body 里只放对方 lane 的动作、自己的动作写在闭标签
  之后(占道见契约文档). 反例是"两边都写进 body 里" —— body 里的指令一被扫描就投递, 写在后面
  不等于等在后面(`t_cross_lane_no_wait` 把这条钉成活证据).
  **顺带补上一个真缺口**: `send(eof)` 不阻塞, shell 发完就继续投下一标签, 而容器要
  收完 body 才 `release` 入队 —— 单组时侥幸正确, 多组连续时顺序全凭调度偶然. 现在
  闭标签处 `req(容器,'ready')` 握手, 容器在 `release` 完成后应答(`_queued` 事件),
  于是"后续同轨标签排在容器后面"是结构保证. 握手失败只记 warning 不产 error 帧 ——
  内部时序故障模型修不了, 不该占它的矫正额度.
  eof 处理顺序也改成"先 `release` 再唤醒 `run`": 反过来时空 body 可能整个跑完才被
  排进队列(已收摊的实例还要被 worker 取一次).

- **`code`（2026-10-19，用户两次校正后定形）**: 一个 body 是源码的普通命令 ——
  同一个 verb 入口 / body 协议 / 回执链路，进 lane 队列、占槽位、跑完才完成。
  body 里的 `call` / `say` **直接建子实例**（`ctx.call` / `ctx.create`），不经过
  `shell.route`、不进 lane 队列，也不产生根账条目（代码是黑盒，账本上只有它
  自己那一条 `{ok, stdout, result}`）。
  - **为什么不走投递**：代码里 `await` 本身就是排序、`gather` 就是并行，不需要
    轨道记号；lane 是给"标签是并行流、需要排序符号"准备的。绕过它之后，"编排者
    与同轨标签并行 / 必须 `call_soon`"这一整套特例自动消失。
  - **子实例的父是 code**，停止/打断走 kernel `_stop_node` 的既有递归级联，
    不需要额外通知通道（route 方案下子的父是 channel，收尾还得另想办法）。
  - body 协议同 say，收完才编译；`PyCF_ALLOW_TOP_LEVEL_AWAIT` 放行顶层 await
    （不用 `async def` + `asyncio.run`）；末表达式改写 `__result__`（REPL 直觉）。
  - 代码面五个名字：`call`（`ctx.call`，失败抛 `CallError`）/ `say`（`create` +
    send text/eof + wait）/ `sleep`（`asyncio.sleep`）/ `publish`（旁路）/
    `print`（本实例 StringIO，不动全局 stdout）。
  - 代价：内部调用不在根账里；也没有 lane 的"物理通道串行"保护 —— 同时 await
    两个动作是真并发（这正是 `gather` 的意义），争用由模型自己负责。
  - 用例 58 → **65**（`t_code_cell` / `t_code_occupies_lane` / `t_code_error` /
    `t_code_unknown_verb` / `t_code_syntax` / `t_code_say` / `t_code_interrupt`）。
    `t_code_cell` 断言"子实例真跑了但从根账缺席"；`t_code_occupies_lane` 断言
    "同轨后续标签等 code 跑完"（普通命令占位）。
  - 历史（第一版，已整体回退）：曾走 `shell.route` + `call_soon` + 回执转投
    （`_fwd`/`_flush`/`_early`）。那条路把 code 抬成"第二套调度前端"，与"普通
    命令"的定位冲突。**附带产出的 channel 补丁保留**：`_soon_handles` 覆盖
    `abort`/`kill`/`channel.stop` 三条收尾路径 + 清道夫显式豁免 —— 对
    `clear`/`interrupt` 这两个仍然 `call_soon` 的指令是必需的。
  - 已知边界：eval 在本进程事件循环里，纯 CPU 的 cell（死循环/同步阻塞）会冻住
    arena/TTS/ASR 且取消不生效（根治要换独立进程宿主；kernel 的 owner 死亡级联
    已在同批落地）；无持久命名空间；回执粒度是一个 cell 一条。
  用例: 56 → **58**(+`t_cross_lane_barrier`/`t_cross_lane_no_wait`).

- **code 内核化: 跨格命名空间(2026-10-19 用户"实际使用场景下是串行的, 能不能做成
  ipython 那样")**: 执行从 code 实例搬进常驻 `CodeKernel`(shell 的子实例, 不入
  词表, 模型看不见). 理由只有一条 —— **一份活着的 globals**: 一个标签一个实例等于
  每格重启解释器, 而"上一格定义的变量下一格还在"是 IPython 手感的一半. `route`
  按 `meta.needs_kernel` 注入内核 rid(与容器拿 `_shell`/`_lane` 同一种编排参数,
  不是特例分支). **串行靠锁兑现**: `@request` handler 是并发的, 两个 cell 同时写
  一份 ns 会丢更新(`x = x + 1`), 所以内核里一把锁 —— 跨 lane 的 code 也排成一列;
  粒度是**一整格**. **子实例的父仍是那一格的 code**: 框架 `ctx.create` 新增
  `parent_id` 可选参(默认自己), 内核代建时指过去, 于是"停 code"照样级联收掉格子
  里打出去的动作; 若让子挂内核名下, 打断就漏动作(用例 `t_code_kids_under_cell`
  正是这条判据). *这条只管**格内 `await call`** 的孩子; `spawn` 起的后台任务
  另算 —— 它脱格存活, 父是内核(见"后台任务的父是内核").* 命名空间活到 moss 停(shell 停 → 级联停 → ns 一起丢); 每格执行前
  `pop('__result__')` 防上一格的结果冒充这一格. 用例 65 → **68**(+`t_code_shares_ns`
  /`t_code_kernel_serializes`/`t_code_kids_under_cell`). 已知边界: 锁粒度一整格
  (长格拖住后面, 要并行就在格内 `gather`); 同名变量跨格覆盖、只增不减; 纯 CPU 仍
  冻主进程(内核已是"一个 hub 就能搬走"的形态: ns 在自己进程里, 跨进程只传
  `{cell, src}` 与回执).
  **字段撤销(2026-10-20 用户"'needs_kernel': True, 这个字段不需要吧")**: 上面那套
  "声明 + 注入"删掉了 —— 内核 rid 改由 code 自己按名字解析(`commands/code/code.py::
  _kernel_rid`, 与 `create_chan`/`capability` 找 shell 同一套). 判据: `always_observe`
  / `call_soon` 是**投递方必须当场知道**的属性(改变指令怎么进轨), 而"要什么宿主"是
  **动作自己的依赖** —— 声明一份 + 注入一份, 两处必须成对, 漏一处就是"code 拿不到
  内核"的哑巴故障; 让需要的一方自己去找, 声明与注入都不必存在. 顺带 `_scan_meta`
  三元组回到两元, `_route` 少一条分支.

- **chan 族: 轨道管理三扇门(2026-10-20 用户"chan 应该搞成 chan/create list status
  delete clear 系列? 放在 chan 文件夹下面?")**: 开轨从单个 `create_chan` 长成一族 ——
  `commands/chan/`(共享薄壳 `ChanCommand`: 找 shell + op + 点灯), 词表三个名字
  `chan_create` / `chan_list` / `chan_delete`, 三者都是同一张轨表上的门, 差别只在
  shell `chan` 控制面的 op(目录即模块边界, 名字带同一前缀, 清单里读起来是一族).
  **clear 不进这族**: `<clear chan="b"/>` 已经是它 —— 同一件事不开两个入口, 否则模型
  要在"清轨"与"轨管理"之间猜该用哪个. **status 不单开动作**: `chan_list` 回执每轨带
  `{queued, running, soon, deferred}`, 带 `name` 只看一条 —— 同一张表的两种粒度, 不是
  两个动作(模型不该为看一眼状态往返两次). **delete 只收空轨**: 队列非空时
  `channel.stop()` 卡在 `_jobs.join()` 上等活跑完, 那是挂住调用方不是回收 —— 有活当场
  拒绝, 并把"先 clear"写进错误; `main` 不许删(装配预建的默认轨). 机制侧: channel 新增
  `@request('status')`(只读, 不碰队列); **shell `_lanes` 改存 handle 不再存 id**(删轨要
  "谁启动谁停止" `h.stop()`, 只留 id 就够不到实例; 投递处取 `h.id`). 用例 74 → **75**
  (+`t_chan_admin`: list 带状态 / 空轨可删 / 有活拒删 / main 拒删 / 删后投递报
  unknown lane), `t_chan_lifecycle` 同步改名.
  **事后更正(同日, 用户"list 那个叫 status 是不是更合适?")**: `chan_list` →
  **`chan_status`**, 控制面 op 也从 `list` 改 `status` —— 动作名 / op / channel 的
  `@request('status')` 三层同名, 中间没有翻译层; 且它交付的确实是运行态(堵不堵)而
  不是资源枚举(给 id 供引用). "status 不单开动作"这个结论不变: 看状态就这一个动作,
  只是名字换对了. `clear` 当时仍不进族, 判据是"本族改轨表(拓扑) / clear 改轨里流量,
  且它是原版原语" —— **该判断在同一条对话里被推翻, 见下**.
  **再次更正(同日, 用户"我觉得这里不需要对照原版")**: 那条判据有一半不成立 ——
  chan 族本来就是本项目自造的轨道管理面, 原版里没有 `chan_create` / `chan_status` /
  `chan_delete`, 拿"原版对齐"去否决 clear 的归属是串了两套标准. 去掉它之后只剩对象判据,
  而对象判据给出的结论正好相反: **族内每个动作都针对一条具体的轨(都要 name)**,
  `clear` 说得出一条轨就该在族里, `interrupt` 说不出(全停)才该留在外面. 于是:
  `commands/chan/clear.py` 新增 **`chan_clear name="b"`**(`op='clear'`, `call_soon`,
  `always_observe=False` —— 中断类不点灯), 旧 `clear` 连同它的无参形态一起收掉
  (`<clear/>` 与 `<interrupt/>` 语义完全相同, 两个名字指一件事; 只保留 `interrupt`).
  判别只剩一条: 说得出轨名用 `chan_clear`, 说不出用 `interrupt`. `commands/control.py`
  现在只有 `Interrupt`(不再继承 Clear, 自己发 `req shell clear {chans: []}`);
  `ChanCommand` 长出 `_ctl(event, payload)`(轨表 `chan` / 清轨 `clear`, 两个控制面入口);
  `chan_delete` 的拒绝回执与 meta 文案改为 `<chan_clear name="b"/>`. 用例仍 **75**
  (`t_clear_drops_queue` 改用 `<main:chan_clear name="main"/>`).

- **调用形态统一: 句柄是唯一入口(2026-10-19 用户"routine 调用需要统一, say 有些
  特殊了")**: 所有 routine 只有一种拿法 —— `create` 拿句柄自己驱动(`start` /
  `feed` / `eof` / `stop` / `destroy` / `await h`), 或者 `call` 一步到底
  (create → start → 递 body → 等结果). **body 是消息面, 不是入参**: `call` 的
  `body=` 与标签中间那段同义(`await call('say', body='你好')` ≡ `<main:say>你好</main:say>`),
  于是 say 不再需要专用调用函数(`_say` 删除), 代码面只剩 `call` / `create` /
  `publish` / `print` + 真模块 `asyncio`(同批也删了 `sleep` 纯别名). 分工:
  框架 `RoutineHandle` 只多一个 `send(任意数据)`, body 的 `feed`/`eof` 长在 moss
  侧的 `CellHandle` 上, 经 `ctx.create(handle_cls=...)` 注入 —— 框架不多懂一个
  概念. **边界**: 容器在代码里没有意义(子要投给 `route` 才有轨可锚), `create`
  遇到容器名当场报 "needs a lane" 并指向 `gather`. 用例 68 → **70**
  (+`t_code_handle`/`t_code_container_refused`).

- **body 的规范(2026-10-19 用户"后面可能会有很多这种 body 带内容的 ctml, 通过代码的
  话如何统一使用他们")**: 判据一句话 —— **kwargs 放参数(短/结构化/可枚举), body 放
  内容(长/可流式/可能就是源码或整篇文章)**, 别互相塞错地方. 作者侧三件事:
  `meta['body'] = 'body 是什么'` 声明 → `on_message` 认 `{'text'}` + `{'eof'}` →
  "边收边干(say)还是收完才开工(code/loop)" 自己定, **协议不变, 只是解释不同**.
  调用侧只有两种写法: `call(name, body=...)`(str / 序列 / 同步·异步生成器, 异步
  生成器边产边递)或 `create` + `feed/eof`(要中途插手), 永不为单个动作造专用函数.
  **闸门**: 没声明 body 的动作收到体当场报 `takes no body`(`call` 与句柄两条路都拦;
  catalog 扫不到的热挂动词不拦 —— 不知道就不猜). **可见性**: 能力清单标
  `⟨body: ...⟩`, 模型才知道该写 body 而不是猜参数名. 用例 70 → **71**
  (+`t_code_body_refused`, `t_code_say` 加测异步生成器).

- **骨头抽成 `CtmlRoutine`(2026-10-19 用户"抽一个基类叫 ctmlroutine?")**: 四个带 body
  的消费者(say/code/容器/loop)各写了一遍同一段: 认两条消息、攒原文、eof 幂等、
  被打断时放行等 body 的 `run`. 基类(`ctml.py`)只收骨头、不解释一个字 —— 子类回答
  `on_body_chunk`(默认只攒 = 收完才开工)与 `on_body_done`(默认不动), 外加
  `body_text` / `_body_ready` / `abort()`. 各命令删掉自己的 `_parts`/`_src`/`_raw`/
  `_done`/`_ready`/`_closed`/`_stopped`, 净少 ~70 行; `eof` 收尾失败也置
  `_body_ready`(钩子炸了不该把 run 卡死). **坑**: 钩子按 MRO 继承 —— `loop` 必须
  显式覆盖成空的 `on_body_chunk`, 否则继承容器的"边到边投递", body 投 1 遍 +
  展开 N 遍(`t_loop_times` 抓到 4 次). 新增 `tests/test_ctml.py` 5 例纯逻辑.

- **语法错回执连源行一起还(2026-10-20 用户"Moss 说第 1 行读到全角逗号, 是他写错了还是
  我们的 bug")**: 查证结论 = **不是本系统的 bug**, 三份证据: ①体路径无任何规范化
  (`body.py` 的 `Text` 事件是原串切片, `CtmlRoutine.body_text` 是 `''.join`, 全仓无
  `unicodedata`/NFKC), 全角逗号只可能是模型自己打的; ②报错措辞是 CPython 的原话
  (实测 `invalid character '，' (U+FF0C)`), 我们只转述, 从不说"全角逗号"这个词;
  ③ASCII 逗号复核 → 编译通过, 根本不产生该报错. 但顺着查出一个真缺口: 回执原来只有
  `(line 1)`, 而**body 不进回执 → 模型看不见自己刚发出去的源码**, 只能凭记忆重写, 于是
  "是不是宿主改了我的代码"这种问题必然会被问. 现在 `_syntax_error()` 附**出错行 + 指针**
  (超长行只留出错点附近 200 字符, 指针跟着挪):
  `SyntaxError: invalid character '，' (U+FF0C) (line 1)` / `  import time，asyncio` /
  `             ^`.   `t_code_syntax` 加断言: 原始行回显 + 指针正好落在那一个字符上.

- **用语只有一个: routine(2026-10-20 用户"万物皆routine, 动作也属于routine, 所以我们
  不需要新建一个 cmd/动作 的概念, 提示词要优化下, 文件夹里的 commands 只是分类")**:
  教学面(`persona.md` / `protocol.md` / 15 个 routine 的 meta)里的"动作/命令/指令"
  全部改成 **routine** —— 不是改词, 是拆掉一个**假概念**: 出现两个名词, 模型就会以为
  存在两种调用方式与两套生命周期, 于是去猜(猜得出写法, 猜不出语义). `commands/` 保留,
  定位写进 `commands/__init__.py` 的 docstring: **分类目录, 不是概念**.
  顺带把 meta 的调用语法也剥了(`<say>…</say>` / `<interrupt/>` / `<b:容器名>` 那些
  行 → 散文): meta 是**能力名片**, 拼法由 `protocol.md` 统一讲, 且接入的 routine 未必
  跑在同一前端(上一轮定的中立规则, 这轮落实到每一行).
  护栏: `test_prompt.t_one_noun`(第 5 例)扫两个 md + 15 个类的
  `description`/`body`/`example`, 命中"动作/命令/指令"或标签语法即红.
  **连带修掉两个真 bug**: ①`BUILTIN_VERBS` 里 `clear` 一直没撤(归族成 `chan_clear`
  后忘的) → 清单里多一行**没有实例的裸名字**; 现在撤掉, 并且 `capability` 对"词表有
  名字、catalog 没注册"打 warning(哑巴不能只哑着). ②`shell` 无条件拉代码内核 → 没装
  `code` 的身体每轮在日志里喊 `code kernel boot failed` ERROR; 现在只在 `code` 进
  词表时拉.

- **body 就叫 body(2026-10-20 用户"体应该是body吧? xml里面的也叫body 这是标注叫法吗")**:
  对, "体"是我自己起的中文简称, 不是行话. 同一个东西在代码面早有真名 ——
  `meta['body']` / `call(name, body=...)` / `on_body_chunk` / `body_text` ——
  标签中间那段就是它的 body(与 HTTP body / request body 同一个意思). 没有第二个
  叫法, 也就没有"标签体"这种"标注叫法"存在的必要: 它只是我把 body 翻成中文时随口
  捏的缩写, 而**模型要学的词多一个, 猜错的机会就多一次**.
  改法: 能力清单渲染 `⟨体: …⟩` → `⟨body: …⟩`(这行是给模型看的, 中英映射省了);
  `protocol.md` / `ctml.py` / `container.py` / `code_kernel.py` / `body.py` /
  `say` / `code` / `chan_status` / 文档里的"吃体·收体·喂体·体里·体是·体增量"
  一律 body. **"身体"不动** —— 那是 shell 的隐喻(身体替它行动), 与 body 无关.
  护栏: `test_prompt.t_one_noun` 追加窄组合断言(`⟨体|标签体|吃体|收体|喂体|体是|
  体里|体内|体边…`), 先剔掉身体/具体/整体等固有词再扫, 防术语回潮.

- **code 不能套 code(2026-10-20 用户"code怎么使用body?" 顺带查出来的哑巴故障)**:
  锁的范围是**一整格**(`async with self._lock` 圈住整个 exec), 于是在格里
  `await call('code', body=...)` 是环等自己 —— 外层握锁等内层, 内层等锁, 一直等到
  外层超时, 而回执只有一句 `exec failed: req 'exec' … timeout`, 看不出是自己等自己
  (实测: `<code timeout="5">await call("code", body="1+1")</code>` → 5s 后超时).
  修法不是让锁可重入, 而是**当场拒绝并说清写法**: `_SAME_KERNEL = {'code'}` 闸门在
  `_create` 里(与容器那句 `needs a lane` 同一位置、同一语气), 文案
  `code cannot nest (one cell at a time, one namespace); write the source inline`.
  判据是"执行宿主是谁"——将来若有第二个走内核的 routine, 加进这个集合.
  嵌套本来也没有收益: 同一份 ns、同一把锁、同一个父, 与写在同一格完全同义.
  用例 `t_code_no_nest`(第 76 例).

- **code 成包 + example 进 md(2026-10-20 用户"code routine改成文件夹, example改成md,
  meta里读取组装")**: `commands/code.py` → `commands/code/{__init__.py, code.py,
  example.md}`. 包名不变, 所以调用方一行没动(`from .commands.code import Code` 走的是
  包); 相对导入深一层(`...ctml`). `meta['example']` 改为 import 期读 md
  (`.strip('\n')`) —— meta 里仍是**纯字符串**, 渲染层不必知道文案住哪. 判据写在包的
  docstring: **多行成段**的 example 才进 md, 一两行短句留在 meta(文件系统也是注释,
  用多了读的人要跳).
  顺带修一个视觉 bug: py 里两个字符串字面量之间的空行**不是字符**, 所以原来代码段与
  说明之间没有空行(用户删掉"(上面就是一个 cell 的 body…)"后想用空行分隔, 实际不生效).
  搬进 md 后空行是真的, 渲染里分开了.

- **example 分节 + protocol 单开 body 一节(2026-10-20 用户"example可以写的更规范精炼
  现在很啰嗦" → "body要单独一节说明" → "需要给个ctml示例 什么是body 有对应关系")**:
  `code/example.md` 改成三个粗体小节 —— 一格(写什么) / body(是什么、怎么喂) /
  叫不动·注意(边界); 删掉与 `description` 重复的话(收完才执行、跨格变量、可用名字),
  一个事实只说一处.
  `protocol.md` 新增 `## body`: **属性是参数, 中间那段是 body**, 用占位名给对照
  (自闭合 vs 带 body 两行), 并写清"没标 `⟨body: …⟩` 的不收 body, 给了会被拒绝"、
  "边到边送达, 边收边干还是收完才动手看它自己". 原来散在 bullet 与"时机"里的
  同一件事收口到这一节, 避免两处各说一半.
  判据: **前端中立**约束的是"不要示范某一种前端的调用写法", 不是"不许说 body" ——
  body 的语义(内容 vs 参数)两个前端都成立, 属于可以留在 meta 的话; 标签形态归
  protocol. (护栏 `t_one_noun` 的标签正则仍在管这件事.)

- **code 占模块 + example 双侧对照(2026-10-20 用户"code可以加一个module 这样并行直接
  抛错,另外example这里加ctml 及其body再对应到code就比较好理解")**:
  0. 新 `modules.py`: 身体的模块树**单一出处**(`MODULES`), `run.py`/测试的
     `kernel.create` 都从这里取. 树里只有一处: root → `code`.
  1. `Code.modules()` 返回 `Modules(['code'])` —— 一台解释器一份命名空间, 一次一格.
     **判据**: 只有 kernel 那把锁时, 第二个格在锁上**静默排队**(看起来像并行, 出事
     只有一句 `exec timeout`); 声明成模块后第二个在 **start** 被 `TryAcquire` 拒,
     错误帧当场回灌 —— 模型这一拍就知道"一次只有一格". 这是"机制级拒绝"与"排队等"
     的差别, 不是优雅问题.
     代价与坑: 树里没有该模块时 `start` 抛 `KeyError`(当场炸, 不是静默), 所以入口
     与测试必须用同一棵树 —— 这话写进了 `modules.py` 的 docstring.
     用例 `t_code_kernel_serializes` 反转成 `t_code_one_cell_at_a_time`(一成一败,
     n 只加一次); 并行语义从"串行跑完"变成"第二个被拒".
  2. `CodeInput(BaseModel)` 补 `input_schema`: `timeout` 以前只读 kwargs、签名里不印,
     模型看不见(而且 example 里教 `<code timeout>` 就成了"只有标签面知道"). 现在渲染
     `code(timeout=600.0)`, 两件事一起说.
  3. `code/example.md` 加**双侧对照**(用户点名的写法): `<code>print(1)</code> ⇔
     call('code', body='print(1)')`、`<say>你好</say> ⇔ call('say', body='你好')`.
     护栏随之改判据: **不是"meta 里不许有标签语法", 而是"不许只给一边"** ——
     description/body 仍必须中立(它们是名片), example 的每一行带标签就必须给对应
     的代码写法(`⇔` 或 `call(`), 否则模型会把某个前端当成唯一说法.
  `protocol.md` 同时补了 `## body` 一节(属性是参数, 中间那段是内容; 没标
  `⟨body: …⟩` 的不收 body), 把原来散在 bullet 与"时机"里的同一件事收口.


## 改名: `wait="observed"` → `observe="true"`(2026-10-20 用户"要跟功能语义对齐")
- **原名字从头就在说谎**。`wait` 搬自原版 `wait_tasks(to_be_observed=True)` —— 那边是
  拍尾**真等**观测类任务; 我们在 2026-10-16 拍板"指令结果不触发"时把悬等撤了
  (`_finish` 明写"不等在途"), **"等"的语义已经不存在, 名字留了下来**。实测证据链:
  `_route` 只做一次字符串比较再把 rid 记进 `_observing`(不问实例、不发消息、不持 future)
  → `_record_trace` 在**实例完成报账那一刻**才消费这个标记盖 `owed` → ghost 拍开着就
  本拍续一拍、拍已收就敲裁判补一拍。自始至终没有任何东西被 wait。
- **新名字对齐两件事**: ① 与 `meta['always_observe']` 同族 —— 一个说"这一次我要它的
  结果", 一个说"每次都要"; ② 值取布尔 `true`, 不再编码"什么时点"(目前只有"结束"一种
  时点, 写 `done` 是多余信息, 用户当场指出"observe=true 不就行了")。
- **不设兼容层**(2026-10-20 用户"不要兼容 我最讨厌兼容"): 旧名既不当别名, 也不写
  "旧名已改名"的护旧报错 —— `<x wait="observed"/>` 就是一个不认识 `observe` 的普通
  调用, 按常规未知参数规则处置。理由: 兼容代码是**给不存在的用户写的** —— `wait` 只
  出现在我们自己的测试与当日文档里, 没有一行外部用法值得养一个分支; 而护旧报错还要
  跟着新名字一起维护, 将来删又得再改一次。
- 但仍守**非法值**(`observe="always"`)当场报错: 那是 `observe` 自己的校验, 不是兼容。
  理由: pydantic 输入不 forbid 未知字段, 拼错的值静默丢掉等于"声明没生效却没人说",
  模型会以为自己认领了债, 等来一个永远不来的拍。用例 `t_observe_value_guard` 守这条。
- 同步面: `shell._route`(唯一判定处) / `protocol.md`(观测回灌段, 并补"它不阻塞任何
  东西 —— 这一拍该收就收") / `orchestration.md`(注意债一节加了"为什么叫 observe 不叫
  wait") / 四个 meta docstring / `t_observe_wait` → `t_observe_claim`。
  历史条目(原版考据、10-17 落地记录)保留旧名 —— 那时候确实叫这个。

## 热挂也要重扫 meta(2026-10-20, 由"meta 声明是不是等于自动填 observe"问出来)
- **缺口是真的**: `_always_observe` / `_call_soon` 是装配期按词表算的快照, 而
  `registry` 的 reg/unreg 只动 `_verbs` —— 热挂一个声明了 `always_observe` 的
  routine, 名字能叫起、回执却永远没债, 模型以为结果会自己回来. 没人报错.
- 修法: 把"按 catalog 重算两个集合"抽成 `_apply_meta`(装配期与热变共用一份算法),
  reg/unreg 后调 `_rescan_meta`; **扫不到则保持旧值** —— 清空等于把所有出厂声明
  一起降级成"无债/不排队", 比不刷更糟.
- 实证: 新用例 `t_hot_meta_rescan`(reg 后回执 `owed=True`). 临时去掉重扫再跑 →
  `AssertionError: {... 'owed': False}`, 恢复即绿 —— 用例确实抓得住这个洞.
- 顺带确认用户原问的语义: meta 的 `always_observe` **不等于"把属性填成 true"** ——
  它不经过标签、不进 args(动作的 pydantic 不认识这个键), 两者是在 `_route` 里
  `observe = 调用处认领 or verb in _always_observe` 合流到同一个 `owed`
  (第三源是结果自报 `{'observe': True}`, 也不经标签).

## 三侧统一成 `observe`(2026-10-20 用户"meta里面也统一字段名叫observer吧, 三侧统一更好")
- **问题**: 改名只改了标签属性, 出厂声明还叫 `meta['always_observe']`, 结果自报又叫
  `{'observe': True}` —— 同一个概念三个名字, 读代码的人要自己发现它们是同一件事.
- **落位**: 三处一律 `observe` —— 标签 `observe="true"` / `meta['observe'] = True` /
  结果 `{'observe': True}`. 差别只剩**时机**(这一次 / 每次 / 结果自己说), 不再有
  词汇差别. 内部集合 `_always_observe` → `_observe_verbs`(避开已有的 `_observing`,
  后者装的是"这次认领过的 rid").
- **为什么不需要 `always_` 前缀**: meta 的语境本身就是"这条动作出厂即如此", `always`
  是重复一遍语境; 而三处同名之后, 合并式 `observe = claim == 'true' or verb in
  _observe_verbs` 读起来就是"调用处认领 or 出厂认领".
- 用户口头写的 `observer` 当场确认为 `observe`(名词读作"观察者=true"读不通; 动词才
  对得上"要它的结果").
- 不做: 不保留 `always_observe` 别名, 不做键名迁移报错 —— 与本日"不设兼容层"同一刀.
- 同步面: `shell.py`(scan/apply/rescan/route/`_record_trace` 四处 docstring + 装配
  log) / `commands/chan/base.py`(族共同声明) / `chan/clear.py`(False 覆盖) /
  `code/code.py` / `query_weather.py`(样板) / `orchestration.md`(新增"三侧同名"一段) /
  `ROADMAP.md` 20 日那行 / 用例 `t_always_observe` → `t_meta_observe`.
  `decisions.md` 与 `ROADMAP.md` 的**历史条目**保留 `always_observe` —— 那时候确实叫
  这个, 台账不重写过去.

## ptt 的段边界归"手"(2026-10-20 用户"ptt模式下应该由我的按下松开去决定?")
- **实测现象**: 按住说话, 中途停顿 1.5s, 判停把半句交了(`complete=True
  '去看看我电脑'` @05.899), 0.13s 后服务端继续吐 partial 又开新段, 松手
  (`ptt up` @06.805)交了后半句 —— 一句话两条交付, 后一条还带着前一条的前缀.
- **判据**: ptt 模式下用户已经用"按住/松手"给出了**显式**边界; 判停(静音定时器 /
  分类器评分 / 关键词)是给 always 模式"猜"边界用的第二把刀. 两把刀抢同一个边界,
  结果只能是切在半句话上. **有显式信号时, 不许再用推断信号.**
- **落位**: `ListenerController.set_manual_boundary(on)` —— 开则 `_open_judge` 三样
  全让位(`segment_vad=inf` / `caller=None` / `keywords=[]`), 关则按礼仪恢复. 由
  asr 在 `run` 起手与 `<asr_mode>` 切换时按 `self._mode == 'ptt'` 调. 切模式**不丢字**
  (文本与分句在 controller 手里, 只换判停策略).
- **`inf` 而不是"很大"**: `StopJudge._on_clause` 遇到 `segment_vad=inf` 干脆不启动
  静音任务 —— 起一个永远不到期的定时器只是白烧一个 20Hz 空转.
- **唯一出口**: 手动边界下 `send_now()`(松手 → `_flush` → `judge.commit_now()`)是
  唯一能关段的地方; 没有静音兜底, 也不设"忘松手"超时(按住 10 分钟也是同一段).
- **连带修的真 bug**: `StopJudge` 的静音定时器原来只在 CLAUSE 展期, PARTIAL 不展期
  —— 服务端迟迟不吐新 utterance 时, 人还在说也被切. PARTIAL/FIRST 现在也刷新计时
  (静音的意思是"没声音", partial 恰恰证明有声). 这条与 ptt 无关, always 模式同样受益.
- 用例: `t_ptt_manual_boundary`(松手前不切段 / 松手即交 / 切回恢复判停) +
  `t_stop_partial_extends_silence`; 两例都做过反向验证(改回旧行为即红).
- **未做**: 切段后新段用服务端 partial 全文覆盖文本, 会带已交付的前缀 → 两段内容重复.
  需要"段起点偏移", ptt 下窗口只有 0.15s 排空期, 暂不动.

## 听音模式默认 ptt(2026-10-20 用户"默认ptt")
- **触发**: 主进程重启后 `asr_mode` 的 ptt 丢了(它是运行期状态, 装配默认
  `always`), 用户按住 f9 以为在 ptt —— 实际麦常开, 句子照旧被 1.5s 静音切成两段,
  第二段的起话包还把正在回答第一段的 `think` 打断了。`always` 下 `key_listen`
  连键都不监听、press/release 被 ignored 且不打日志, 现场看不出"没切成功"。
- **落位**: `AsrInput.mode` 默认 `'always'` → `'ptt'`; `Asr.__init__` 的
  `_mode/_button` 不再硬写, 直接取 `self._cfg` 的值(默认值只留一处)。
- **为什么是默认而不是持久化**: 持久化要引入状态文件、要定"谁写谁读"、要处理
  写坏的情况; 而"我这台机器就是按住说话"本来就是装配期决定 —— 默认 ptt 就是
  把这条决定写进装配参数, 不做运行期存储。
- **代价**: `always` 侧变成要显式 `<asr_mode mode="always"/>`。所以
  `t_asr_intake` / `t_onset_absorb` 这两个测"always 侧入关策略"的用例开头都补了
  一句 `req('asr','mode',{'mode':'always'})` —— 它们要的坐标不是默认值。
- 记事: 用户若偶尔想常开麦, 一条 `<asr_mode mode="always"/>` 即可, 但它**不跨重启**
  (仍然是运行期状态); 需要跨重启就得回到"持久化"这条路, 到时候再拍。

## 静音的两个主人(2026-10-20 用户贴 `mic muted 120s, 强制放麦`)
- **现象**: ptt 说完松手、正常回复之后 2 分钟, 冒出
  `WARNING mic muted 120s, 强制放麦(疑似丢失 stop 帧)` —— 听着像故障, 其实是
  ptt 的"没按"撞上了**闸门**的兜底.
- **根因**: 麦克风只有一条静音开关, 但有两个主人 —— 闸门(TTS 出声期, 临时)与
  ptt(没按住, 故意且无限期). `_MUTE_MAX_S=120` 的强制放麦本是给闸门兜底
  (stop 帧丢了会永久失聪), 套到 ptt 上就变成"两分钟没人按, 麦自己开了":
  既冤枉(没有 stop 帧可丢), 又违背"按住才听"(音频照样流向服务端, 只是
  识别事件被 `_listening()` 挡掉).
- **落位**: `_Mic.set_mute` / `Ear.mute` 加 `watchdog: bool = True`, 只对闸门静音
  生效; ptt 三处(装配默认闭嘴 / 松手关麦 / 切模式到 ptt)一律 `watchdog=False`.
  期望态连 watchdog 一起记在耳朵上(麦还没建时也不丢).
- **判据**: 兜底是防"**意外的**永久静音"; ptt 的不按是**预期的**永久静音 ——
  兜底只该拦意外.
- 用例 `test_listen.t_mic_watchdog`(闸门超时放麦 / ptt 超时不放 / 期望态透传),
  反向验证过(去掉 watchdog 判定即红).

## 黑板 routine: 形态与落位(2026-10-21 用户拍"一笔一标签 / 画完才回执 / bb_ 前缀")
- **来源**: `blackboard-app/docs/socketio-api.md` —— 一块**静态**黑板(没有播放/队列/
  时间轴), 用户侧推 `bb:up{type:"actions"}` 八个 `do`, 黑板侧回灌 `bb:objects` 对象表.
- **判据(决定了整个形态)**: 黑板对错误是**全静默**的 —— 不校验 payload、没有 ack、
  锚点前缀拼错当 `below`、id 不存在落画布中心. 所以在 moss 里, 这些 routine 是**唯一
  能当场报错**的地方(pydantic + 错误帧 + 当拍矫正). 形态要为"每一笔都能被静态校验"
  服务, 而不是把校验推给一个自己写的解析器.
- **形态**: 一笔一标签(七笔 + 一眼), 只有 `bb_write` 收 body:
    - body = 内容(长/带引号换行/公式), 属性 = 参数(坐标/尺寸/挂靠) —— 照抄项目已有
      的 body 判据; 文本塞属性要转义, 写坏黑板不吭声;
    - 反例 `<bb do="write">` 被否: `do` 的八个取值各有各的字段, 更致命的是**只有 write
      收 body**, 而 body 有无是 routine 的静态属性(闸门 `takes no body` 靠它) ——
      带 `do` 的 routine 会在 `do="circle"` 时把 body 静默吞掉;
    - 整幅画塞进一个 body(迷你 DSL)**没有做**: 那是第二套前端(行语法/错误位置/等待
      语义都要自己定), 而黑板没有时间轴 —— 顺序本来就该由**标签次序与轨道**表达.
- **节奏**: 每个动词推完那一笔,**等它的入场动画时长**再回执(`board/timing.py`),
  于是"标签顺序 = 画面顺序", 模型不用 sleep、不用数字数. 时长是从黑板侧
  (`web/src/board/board.ts` · `geometry.ts`)抄来的常数: write 每字 0.155s、
  circle 固定 ≈1.13s、line 按采样点数夹在 0.3~0.68s、point 0.22s、擦除 0.
  协议**没有**"画完了"这个信号(只有动作到达约 220ms 后的对象表), 所以只能估;
  抄偏只是节奏差一点, 不会画错.
- **并发不归标签管**: 要同时画就换一条轨(`chan_create`), 不加 `now=` 之类开关 ——
  两处管并发就会打架. 这也是 chan 第一次真正有用的地方: 一边说一边画, 本来就是两条轨.
- **id**: 模型自己起(`a1,a2…`), 因为锚点在"写这一句的时候"就要落下, 等不到回执;
  `write/line` 必填(它们进对象表、之后要被引用), 其余可省 —— 省了由 board 编号
  (`x1,x2…`), 那些笔不进对象表, 起了名字也没人指得着.
- **锚点**: `at/from/to` 一个槽一个属性, 值两种写法 —— `"0.12,0.28"`(归一化)或
  `"below:a1"`. 这是唯一为属性扩的语法(属性装不下数组). 前缀只认四个, 拼错当场报错
  (黑板会静默当 below).
- **引用校验**: `circle/underline/erase` 在有对象表快照时先查 id 在不在, 不在就报错;
  **没有快照时不判死**(服务端不缓存历史, 刚连上本来就空).
- **落位**: `board/`(器官: `routine.py` 被动常驻连接持有者 + `client.py` 手写
  EIO=4/Socket.IO v4 + `timing.py`) / `commands/board/`(八扇门) / 进 `BUILTIN_VERBS`.
  惰性连接: 装配只拉起 Board, 第一次 `bb_*` 才连 —— 黑板服务没起也不拖垮启动.
- **验过**: `tests/test_board.py` 8 例(入参/那一笔/等多久/错误); 另起真服务端做端到端
  探针 —— 握手带 `{"role":"user"}`, 动作以 `42["bb:up",{type:"actions"}]` 到达 viewer,
  对象表以 `42["bb:objects",...]` 回灌并落进 `Client.snapshot()`.

## 接口文档升到十二个 do, 族跟着长(2026-10-21 用户"接口文档更新了 看下")
- **变了什么**(`blackboard-app/docs/socketio-api.md`, 权威仍是 `shared/protocol.ts`):
  `circle` 从"圈住某个对象"改成**几何圆**(`at` 圆心 + `r` 半径), 原来那个手绘强调圈
  单独叫 **`ring`**(`around` + `pad`); 新增 `rect` / `poly` / `ellipse` / `pixel`
  —— 共十二个 `do`. 顺带明确了两件事: **两种量两个单位**(位置按轴归一化, 长度按
  **画布短边**, `pad`/`round` 是画布像素), 以及**画形状要用 poly/rect 一整条笔迹**
  (多笔 line 在角上必然断).
- **族的落位**: 十三扇门 —— 十二个 `do` 各一个名字 + `bb_objects`. 命名上把最容易混
  的两处**用名字分开**: `bb_circle`(几何圆) / `bb_ring`(批注圈). 不做兼容: 旧的
  `bb_circle around=…` 不是一个"已改名"的旧写法, 它现在缺 `at`/`r`, 当场报"缺 r".
- **谁进对象表**: write / line / rect / poly / circle / ellipse —— 这六个**必须自己起
  id**(进表就会被引用); arrow / ring / underline / point / pixel 不进, id 可省(省了
  由 board 编号 `x1,x2`). 判据是协议里那句话, 不是"这一笔重不重要".
- **属性装不下的两处, 两种处理**(这是本次唯一的形态决策):
    - `poly.points` = **空白分隔的锚点串**(`"0.5,0.16 0.32,0.52"`)。锚点本来就短,
      一段段写下来模型不用学新语法, 仍是一槽一属性;
    - `pixel.ops` = **body 是 JSON 数组**。ops 是嵌套记录(操作码 + 五个字段), 硬塞
      属性就得发明一套行语法 = 第二套前端; 它本质上是一小段程序, 与 `code` 的
      body=源码同类. 于是族里出现第二个收 body 的名字(`bb_write` / `bb_pixel`),
      按类级 `meta['body']` 声明, 闸门与名片照旧.
- **时长公式跟着改**(`board/timing.py`): 圆/椭圆按半径算采样点(56~240), 矩形/折线
  按周长与角数算, `ring` 固定 151 点(≈1.13s, 与圈的对象多大无关), `underline` 撞到
  0.3 下限. 原来 circle 写死 151 点是错的 —— 那是 ring 的采样数.
- **入参错误翻成人话**: 新增 `base.parse(model, kwargs)`, 把 pydantic 的
  `Field required` 换成 `入参不对: 缺 r —— 每个参数怎么写在能力名片里……`.
  理由与 `chan_clear` 那处同一句: **黑板对坏 payload 全静默, 这里必须能照着改**.
- **没做的事**: 不校"形状会不会画出画布"(黑板自己钳位, 只是难看); 不给 `rect`/`poly`
  加"别和已有对象重叠"的检查(重叠要自己排, 那是布局, 不是接口).

## 十三扇黑板门全族 `observe`(2026-10-21 用户"黑板的操作也该是 observe 啊")
- **判据**: 画面是模型**看不见的那半世界** —— 这一笔画成什么样(实测 bbox 由黑板侧
  回灌)、板上现在有哪些 id(后续 `below:`/`around:` 的唯一抓手), 都只有回执能说.
  不点灯 = "画完那一拍白画", 下一拍得自己想起来再问一次.
- **落位与 chan 族同一条路**: 声明跟着动作走 —— `BbCommand.meta = {'observe': True}`,
  十三扇门各自 `meta = {**BbCommand.meta, …}` 展开(与 `ChanCommand` 一处不差).
  `meta` 是**整块替换不是合并**, 所以"新增一门忘了展开"是唯一的静默失效面:
  `t_observe` 就钉这一条.
- **为什么不设例外**: `chan_clear` 不点灯, 因为它是中断类(清完就清完了). 黑板不打断
  任何东西 —— `bb_erase around="all"` 只是"世界变了", 而且 id 不存在时黑板静默,
  回执是唯一的确认. 族判据是对象(同一块板), 那就一刀切.

## 抢占的判据是"有东西可停", 不是"它最强"(2026-10-21 用户日志"打字发出去不回应")
- **现场**(01:49): 用户打字"用黑板讲个课" → 9s 没有回音(首字 `ttfb=+8317ms`)
  → 补两行"?" / "666" → 界面只剩 `-- interrupted: interrupted` 和一句
  "刚才被打断了，现在开始——黑板讲个短课。"。ghost 记忆里三帧:
  `用黑板讲个课`(logos 空)、`?\n666`(带着完整的黑板课)。
- **根因**: 抢占的检查点在**字流块边界**。首字没到的那 8.3s 里, 脑子还在等
  HTTP, 一个字都没投出去; 此时"?"按普通输入(同源 ×1.1 强度)赢了衰减中的
  现任 → `_preempt` → abort 置位 → **首字一到就在那里抛错**, 整拍零产出。
  用户看到的是"我发的消息没有被回应", 而他补的那两句反而顶了上去。
- **判据**: 抢占的对象是**"它正在说的话"**, 不是"它还没想完的脑子"。
  没有输出 = 没有可停的东西。所以灵魂新增只读面 `state`(beat / open /
  spoke), 桥梁在 `win` 之后补一问: 拍开着且没开口 → verdict = `defer`,
  新输入**回检录缓冲排队**, 等这一拍说完自己成下一届(链终的 `_wake_or_idle`
  会点灯, 不另设信号)。
- **边界**: 显式 `interrupt=True` / `FATAL` 不受此限 —— 那是"停"的语义承诺,
  不是竞争; `knock` 也不排队(它整个身份就是"忙时没听见就没听见", 排队等于
  把可丢变成必达)。问不到灵魂(它整个没了)按"有东西可停"处置: 宁可抢占,
  不留一个打不断的身体。
- **代价照实说**: 用户边想边说(纠正/追加)时, 新句只能等下一拍, 不像原来那样
  当场切掉旧拍。判据是"这 8 秒值不值得白扔": 白扔的那一拍不但没回音, 还会让
  用户以为自己没发出去。要改回"打字永远打断", 改 `_challenge_incumbent` 一处。
- **验过**: `t_defer_silent`(空块期 declare 不抢, 两句各自成拍, 无 interrupted
  留痕) / `t_preempt`(FATAL 照旧抢) / `t_barge_in`(interrupt 照旧抢) /
  `t_knock`(忙时照旧丢) / `t_absorb_partial` / `t_go_revive` / `t_echo_lamp`
  全绿。

## 思考要看得见, 但不算"话"(2026-10-21 用户"思考没有渲染出来吧? ui要补")
- **现场**: 01:49 那拍首字 `ttfb=+8317ms`, 8.3 秒里 UI 一片空白 —— 用户以为
  没发出去, 补了 "?" 和 "666". 思考流一直在脑子里流, 只是被 `brain.stream`
  的 `if isinstance(ev, TextDelta)` 挡在门外(那句注释写着"思考不进对话流").
- **判据**: 思考是"它还活着"的唯一证据, 必须可见; 但**它不是它说的话** ——
  三个方向都不能去:
  - 不进身体: 进 shell 会被 CTML 解析器当动作执行(草稿变命令);
  - 不进 logos: 落帧就成了"它说过的话", 下一拍模型把自己的草稿当既成事实;
  - 不进 epoch 摘要: 压缩的是经历, 不是草稿.
- **形态**: 一条**旁路**而不是第二路字流. 脑子(可选能力)暴露
  `on_reasoning(text)` 协程属性; 灵魂在 `on_created` 挂上出口
  (`hasattr` 探测, 假脑没有就不挂 —— 这不是兼容旧写法, 是可选能力);
  出口只 `publish('think')`, 且**拍开着才推**(epoch 摘要那种拍外思考不推).
  `spoke` 仍只看正文 —— 用户在思考期补话照样 `defer`, 灰字已经告诉他"它在想".
- **UI**: `moss/think` → 灰(#8a8f9a)斜体 `想: ` 行, 流式追加; 正文/观测/
  我的话/打断任一到就收块. `insertText` 吃 cursor 当前格式, 所以每处插正文
  前显式 `setCharFormat(空格式)` —— 否则思考的斜体会串到对话上.
- **不做**: 思考不折叠/不加时长统计(QTextBrowser 没有现成折叠块, 真要看
  历史去 Moment 侧栏加字段是另一件事); 思考不入 `recall`, 不进提示词.
- **验过**: `test_ui_think`(3 例: 一行流式/正文收块不串样式/每拍一行) +
  `test_brain` 新增 `t_reasoning_bypass`(交错顺序分派, 正文只 yield TextDelta)、
  `t_reasoning_not_in_memory`(广播到 `moss/think`, logos 里没有草稿).

## 回执是事实摘要, 不是状态转储(2026-10-21 用户"怎么这么多垃圾数据 浪费上下文")
- **现场**: `<bb_objects/>` 的回执把黑板对象表**原样**交回: 每个对象带
  `box: [0.060000000000000005, 0.046544342507645255, …]`(17 位小数)、
  外加一个 socket id `from: "QNXcgRYyJzgujcOzAAAF"`. 这些字全都进了下一拍的
  prompt —— 模型复述一句"上面那份快照是擦之前拍的", 上下文里躺着一整张表的浮点.
- **判据**: 一条回执有三个去处 —— UI 显示 / obs 帧入史 / **喂脑子的那一行**.
  前两个可以全量(排障要原文), 第三个必须是摘要. 截断只发生在第三处:
  `Moment` 里存的仍是原始帧.
- **两层**:
  1. **门自己给摘要**(根本): `bb_objects` = `{connected, count, objects:[{id,
     kind, text 前 30 字}]}`; 几何要写 `box="true"` 才给, 且 `round(…, 3)`
     —— 归一化坐标 0.001 已是画布上约两个像素. `from`/`seq` 是内部编号, 删掉.
  2. **脑子兜底**(护栏): `brain._frame_line` 每行 600 字上限, 超出标
     `…(截断, 共 N 字)`. 任何门(含 `code` 的大 stdout)都撑不爆一拍.
- **为什么不是"让模型别查"**: 查是正当的(确认真上板了、我要挂靠谁) —— 错的是
  回执形态, 不是查询. 也别把摘要做成"只有数量": 模型要靠 id 挂靠, 清单必须留 id.
- **代价照实说**: 600 字以上真需要原文的场景(超长 stdout)模型得重跑一次;
  几何默认不在回执里, 想精确排版要多发一次 `box="true"`.
- **验过**: `test_board.t_objects`(清单不带 bbox / box 千分位 / 长文字截断 /
  空表 count=0 / box 只认 "true") + `test_brain.t_frame_clip`(600 上限, 短
  回执原样).

## 回执分两层: 信封说成没成, 载荷说结果是什么(2026-10-21 用户"routine 返回三个字段")

- **现场**: `<bb_objects/>` 把整张对象表(每对象 17 位小数 bbox + socket id)
  原样交回, 全进了下一拍 prompt. 上一轮的对策是 `brain._clip` 600 字截断 ——
  那是**给症状打补丁**: 一个 `result` 字段同时喂三个去处(UI 行 / Moment 原文
  / 模型那一行), 三处要的东西根本不同, 截断只能让最不该长的那处变短.
- **判据(用户拍的分层)**: 帧分两层, 谁也别越界.
  - **信封**(channel 写, shell 记账): `kind` 成没成, `verb`/`chan`/`rid`
    谁在哪条轨, `owed` 要不要点灯. —— 回答"成功了没有".
  - **载荷**(routine 的 `run` 返回): `content` 模型读的一行, `details`
    人看的结构化数据, `observe` 结果自报. —— 回答"结果说了什么".
  失败**没有载荷**: 门做不到它声称要做的事就抛, 信封改写成 `kind='error'`
  并把原因放 `text`. 于是"成没成"永远只有一个判据(信封), 不会再出现
  `{'ok': False}` 这种"载荷里说自己在信封上应该失败"的第二套表示.
- **失败与事实的分界**(这条比字段名重要): **门没做到它声称要做的事 → 抛;
  做到了、但事情本身的结果是坏的 → content 里说**. 按这条落:
  `asr_mode` 的 "asr not running" / `code` 的 "kernel not started" → 抛;
  `code` 里用户代码抛 traceback → **不抛**(执行并报告结果就是门的活,
  报错也是报告, 带 `observe` 点名). 原来那 8 处 `{'ok': False, 'error'}`
  就是靠这条一句话分开的.
- **关门的唯一位置**: `frames.receipt(verb, result)`. 三字段之外的键当场报错
  (channel 收成 error 帧, 当拍看得见) —— 拼错的键安静消失才是最坏结果: 模型
  拿着残缺回执猜它本来想说什么. `details` 在这里试一次 `json.dumps`: 过一个
  不可序列化的东西 = 回执永远到不了模型(`Channel._report` 失败只记日志),
  宁可当场炸.
- **content 缺省 `'ok'`, details 缺省 `{}`**: 琐碎成功(写完了/擦掉了)不必每扇
  门编一句话, 光秃秃的 `bb_underline: ok` 模型照样知道成了.
- **结果**: `details` **没有通往 prompt 的路** —— 不是"截断拦住", 是"没有字段
  可灌". 600 字上限随之退成 4000 的兜底(门自己知道该留多长). 十三个黑板门
  的 content 各写一句: `id=w1` / `圈住 a1` / `擦了全部` / `3 个: a1「…」`.
  `bb_objects` 要 `box="true"` 时必须把几何**写进 content** —— 模型看不见
  details, 只放在里面等于模型要不到.
- **不动**: `channel` 的 `status/kill/abort/release` 与 `code_kernel` 的
  `{ok, stdout, result}` —— 那是内部 RPC 回执, 不是给模型的回执, 混进来就是
  第二套名字.
- **验过**: 新 `test_frames`(4 例: 缺省 / 多键与坏 details 三拒 / 只读 content
  且 details 一个字不漏 / 4000 兜底) + `test_board` 11 例改写(含"box 必须写进
  content") + `dispatch` 79 例并行 x8 全绿.

## 唤醒不需要第二条驱动线: notify 是 declare 的门面(2026-10-21 用户"加一个 notify 的 routine")

- **问**：code 收到外界响应后，能不能调一个 routine 把它叫醒一拍？**能**，
  而且仲裁一行都不用改 —— 缺的从来不是"唤醒能力"（`declare` 就是唯一外界
  入口），缺的是"**不打断现在**"的表达。
- **落法**（三个现成分支刚好接上，不新增模式、不新增字段）：
  1. `_challenge_incumbent` 第三条：`priority == BACKGROUND → 'lose'`
     —— 有现任时**永不抢擂**；
  2. `_dispatch` 第一条分支：`mode == NOTIFY or priority == BACKGROUND`
     → `_inject` 挂账（送达下一拍，永不丢）；
  3. `_safe_chain` 的 finally：`_pending_inject` 非空 → 造一条 `mindflow`
     空冲动开下一届。
  于是**同一个参数覆盖两种状态**：空闲时它是唯一候选 → 直接成链开一拍（这
  就是用户说的"激活自己一拍"）；正在说话 → 一定输 → 挂账 → 说完就答它。
  门**不需要判断链在不在场**，判了就多一处会和仲裁打架的状态。
- **source 缺省独立成 `notify`，绝不借 `'user'`**：同源挑战有
  `SOURCE_ESCALATION = 1.1` 提权，借 user 等于"一拍里发通知 → 打断自己正在
  说的话"。这是这门最容易踩的坑，比参数名重要。
- **`mode="interrupt"` 必须自己带 `effort='normal'`**：裸写 `interrupt=True`
  会被 `declare` 按原版 primitive 补成 `effort='none'`（停而不答），那条
  通知就白送了。门的三档 = notify（缺省，不打断）/ next（不打断但强制下一
  拍看见）/ interrupt（停下现在并回答）。
- **失败口径照旧**：找不到裁判（`mindflow not running`）→ 抛；`verdict` 不是
  `accepted`（dropped/noop/ignored）→ 抛。申报是门的唯一职责，"没送成"就是
  没做到，不能写成一句"已通知"让模型以为送达了。
- **核**：`NotifyNucleus`（`aggregate=False` —— 两条构建结果不该拼成一句话；
  缺省 `BACKGROUND + mode='notify'`）。出厂核从 4 个变 5 个。
- **仍悬着的一件事（真正的工程量在这里）**：**谁持有"等"**。
  - `code` 格内 `await`：整格握着 `AsyncLock`（`_exec_entry` 用 `async with`
    包住 `await task`），等待期间别的 code 全排队，且有 600s 上限；
  - 格内 `asyncio.create_task` 的孤儿任务：不在 `_cells` 里（`cancel`/
    `interrupt` 打断不到），格结束 `self._cell=''` 后它再 `call` 会把父挂到
    下一格 —— 是"打不断的东西"，违反"宁可抢占，不留一个打不断的身体"；
  - 常驻感知源（长期订阅）：按三张门票判据它够格（独立时间轴/隔离/第二消
    费者），但重。
  结论留给下一步：要么给 `code` 加 `spawn()`（后台任务登记进内核，父钉在
  登记那一格，`cancel(cell)` 一起取消，格回执如实报"运行中 N 条"），要么
  把长期订阅做成独立 routine。**本次只落了门，没碰内核**。
- **验过**：`test_notify`（5 例：缺省不抢/next 插队/interrupt 带 normal/
  来源传递/四类失败当场报错）+ `test_prompt`（核表出现 notify，能力清单
  跟着词表渲染）+ `test_ctml` 5 例。

## 临时等待用 create_task 就够, 但孤儿得有两个"非它不可"的兜底(2026-10-21 用户"moss 自己决定 cancel?")

- **问**: 等外部响应要不要给 code 加 `spawn()` 门 / 建一个常驻 routine?
  **不用**. 临时任务 `create_task` 就够 —— 它等的不是 routine, 没有实例可言,
  也**没有门**要开. 停不停归模型(`t.cancel()`), 变量跨格保留所以下一格还找得到它.
- **但裸用有三个洞, 每个都不是洁癖**:
  1. **父钉错**: `self._cell` 是实例属性, 格跑完就清空. 孤儿任务过一会儿再
     `call`, 父变成 `''` —— 一个不属于任何格的子实例, 停 code 级联时收不到它.
     **改法**: `_cell_of` 变 `ContextVar`(格内 `set`/`reset`, `create_task` 复制
     context) → 孤儿自动钉在**发起它的那一格**, 即使那格早跑完了.
     **注(2026-10-21 晚)**: 这条只解决了"钉对哪一格", 没解决"那一格还在不在"
     —— 见下一节, 孤儿现在改挂内核.
  2. **内核收摊漏取消**: `stop()` 原来只 cancel 当前格的 task. 孤儿会活过自己的
     宿主, 在某个随机时刻再投一次 notify —— 那时没有任何人知道它属于谁.
     **改法**: `_bg` 登记表, `stop()` 一起 cancel.
  3. **死讯只进 stderr**: 没人 `await` 的 Task 抛异常, 只有 asyncio 那句
     "Task exception was never retrieved". 模型看不见 = 失败被吞.
     **改法**: done callback 收进 `_bg_fails`, **下一格回执**报"后台任务失败: …"
     (取走即清, 不复述旧事).
- **所以 `spawn` 是代码面函数, 不是门**: 和 `call`/`create`/`print` 并列注入
  (`_host_names`), 干的事只有 `create_task` + 登记 + done callback. 三行,
  零新概念 —— 它不改变 asyncio 的任何语义, 只是"让这条任务留下来报账".
- **回执照实说**: `content` 尾巴 `后台任务运行中 2 条: watch-config, poll-build
  (t.cancel() 可停)`. **必须进 content 不能只进 details** ——
  模型看不见 details, 而"我还有哪些在跑"正是它决定要不要停的唯一依据.
  被打断(interrupt code)时也报: 打断的是这一格, 不代表订阅也停了.
- **不做的**: 不给 `spawn` 加 `cancel`/`list` 门(`t.cancel()` 就是全部接口);
  不让 cell cancel 级联杀 `_bg`(跨格存活是它的目的, 一刀切会杀掉长订阅);
  `spawn` 已在跑的 Task 直接认领(输入校验, 不是兼容旧写法).
- **验过**: `dispatch.t_code_spawn`(报账 / 跨格 cancel 真停 / 死讯走下一格回执);
  `dispatch` 80 例并行 x8 全绿; `test_prompt` / `test_ctml` 全绿.

## 后台任务要"名字 + 在等什么": 回执的读者是下一拍的自己(2026-10-21 用户"spawn 时可以 name/desc 吗")

- **判据**: `name` 是对号的**标识**, `desc` 是**语义** —— 两者不是重复, 缺任一个
  回执就残: 只给名字(`watcher`), 下一拍得翻回起任务那一格才知道它在等什么;
  只给描述, 两条任务之间对不上号(尤其 `t.cancel()` 要对着变量做).
- **形状**: `spawn(coro, name='watch-config', desc='等 config.json 改动')` →
  回执 `后台任务运行中 1 条: watch-config(等 config.json 改动)`.
  死讯同一个形状: `后台任务失败 1 条: bad(等构建) → ValueError: boom` ——
  失败的时候比活着的时候更需要 desc, 因为模型要判断"这错了要不要重起".
- **用账目用语，不用人话**（2026-10-21 用户"这也太口语化了"，原话是
  `后台任务挂了: …`）: 回执是**记账**，不是转述。状态词取 `运行中 / 失败`，
  条目取 `名字(在等什么)`，失败接 `→ 异常`；"还挂着"/"挂了"这类口语不进
  content —— 模型读的是账，越像账越不会被当成叙述去复述。
  （同一条判据也吃掉了 `(各自的变量还在, …)` 这类解释：能做的动作只说
  `t.cancel() 可停`，原理不必在账上写。）
- **同名自动编号 `#2`**, 不报错: 循环里起一批同名任务(`for p in paths:
  spawn(watch(p), name='watch')`)是很自然的写法, 报错会把正常写法变成障碍;
  但回执里两条都叫 `watch` 就报不了账. 编号是**回执可对号**的兑现, 不是兼容.
- **details 也跟着结构化**: `bg: [{name, desc?}]` / `bg_failed: [{name, desc?,
  error}]` —— content 是模型读的一句话, details 是人排障用的表; 两边同一个
  事实, 两种粒度.
- **不做**: 不给 `desc` 设字数上限(routine 的 `meta['description']` 也没有) ——
  它由模型自己控制, 写长了回执自然变长, 而这条回执本来就只该有一两句;
  不让 `desc` 参与对号(那是 name 的唯一职责).
- **验过**: `t_code_spawn` 扩到 4 判据(名字+描述 / 同名编号 / 跨格 cancel /
  死讯带 desc); dispatch 80 例并行 x8 全绿.

## 后台任务的父是内核, 不是那一格(2026-10-21 用户"后台任务的本意就是脱离本格限制")

- **故障**: 一个服务跑在 `spawn` 起来的后台任务里, 它拿到外部事件后调
  `notify` → 回执 `parent routine 21 not found`, 服务端收到了点击但拍起不来,
  界面上什么也没发生.
- **定位**: 报错来自 `routine/kernel/manager.py` 的 `_create_node` ——
  `parent_id` 必须在 `nodes` 表里. 而 21 是当初起这条任务的 `code` 格: exec
  返回 → 实例收尾 → `_stop_node` → `_remove_node`, 节点已摘. 于是**任务还
  活着, 却认不了父**.
- **根因不是"钉错了格", 是"钉格"这件事本身不对**: `spawn` 的存在意义就是
  活过发起它的那次 `exec`(跨格存活 / 停不停归模型). 让子比父长寿, 无论
  钉得多准, 父终归会先没. 判据一句话: **父必须比子活得久**.
- **改法**: `_cell_of` → **`_parent_of`**(语义从"我现在在哪一格"变成"我建的
  孩子挂谁"), 两种宿主各写各的:
  - `_exec_entry` 里 `set(格 id)` → 格内 `await call(...)` 的孩子挂格
    (停 code 照样级联收掉它派出的动作, 中断干净);
  - `_spawn` 里 `copy_context()` → 在**副本**上 `set(本类 id)` →
    `create_task(coro, context=ctx)` → 后台任务派出的孩子一律挂内核,
    与内核同生共死.
  之所以 copy 而不是直接 set: 直接 set 会污染格的上下文, 在 set 与
  `reset(token)` 之间格自己 `call` 就挂错父.
- **代价(这是取舍不是漏洞)**: 停 code **不再**级联收后台任务派出的动作 ——
  这正是"脱格"的本意. 于是"打断不留打不断的身体"对后台任务只能靠
  `t.cancel()` / 停内核兑现, `interrupt` 一拍管不到它.
- **例外**: `spawn` 收到已是 Task/Future 的直接认领那种, 上下文改不了
  (任务已启动), 只能是它原本的父. docstring 里写明, 不当默认行为.
- **不做的**: 不在 `_create` 里"挂不上就重试挂内核"(运行时探测 = 给两套父
  留后门, 且窗口期内行为随宿主的生死变化); 不给父加 `cell=` 归属字段
  (归属靠 `name`/`desc` 对号已经够, 别在树边上塞元数据).
- **验过**: `t_code_spawn` 补判据 —— 后台任务里 `call` 不再 `parent …
  not found`, 且格内 `await call` 的父仍是格(停 code 级联收得到).

## 取消后台任务要连带收掉它派出去的东西(2026-10-21 用户"cancel 的时候不能对应上吗")

- **缺口**: 父改挂内核之后, 框架按父级联的那套**不再覆盖后台任务派出的
  实例** —— `t.cancel()` 只停协程, 它 `await call('hold')` 出来的实例还在
  跑, 变成"没人要却还在动"的身体. 内核其实**当场就知道**建了什么(句柄在
  `_create` 手里), 只是没记账.
- **要记的不是"父", 是"发起者"**: 两者在格内重合(都是格), 在后台任务里
  不重合(父是内核, 发起者是那条任务). 所以另开一个 `_owner_of` 这个
  `ContextVar`: 格内 `set(格)`, `spawn` 时在 copy 出来的 context 上
  `set(bg:N)`(与改父同一份副本). `_create` 末尾把成功的句柄记进
  `_kids[owner]`.
- **收场一律收尸**: `_bg_done`(同步回调)取 `_task_owner`  → 起一条 `_reap`
  协程; 不管 cancelled / 抛错 / 自然结束都收 —— 任务都没了, 它启动的事
  也没人等了.
- **用 `stop()` 不能用 `destroy()`**: `destroy` 只清 **created 态(还没
  start)** 的子实例, 已 start 的会被 kernel 拒(`rejected op=destroy`)——
  而任务里 `call` 出来的实例基本都 start 了. stop 让它走自己的停路径
  (say 截声 / hold 放行), body 结束再照常摘节点.
- **格的账不入 `_reap`**: 格内孩子的父是格, 停 code 时框架级联已经收过了,
  内核再收一遍是双停. 格的账只用来在格收尾时清掉(`_kids.pop(cell)`),
  不留一份永远不用的表 —— 两份机制管同一批孩子只会互相踩.
- **认领进来的 Task 没有 owner**: `spawn` 收到已启动的 Task/Future 改不了
  它的 context, 它派出的实例不记账(= 不会被 cancel 连带收). 与"改不了父"
  是同一个例外的两面, docstring 写明.
- **验过**: 新增 `t_code_bg_reap` —— 后台任务里 `await call('hold')`, 取消
  任务后 `hold.stop` 必须出现(而不是等到用例收尾才被级联停). 负向验证过:
  把 `_kids` 记账去掉 → 断言失败, 确认判据不空转.

## 装配清单化：moss.yaml + 只有一个 say（2026-10-21）

**决定**：moss 的启动名单从 `run.py` 里的 `rs.register(...)` 搬进 `app/moss.yaml`，
入口只剩 `python -m app.main app/moss.yaml`（`run.py` 已删，见下一节）。

- **清单多一段 `modules:`**：那棵树是**身体拓扑**（moss 的 `code` 是独占资源），
  app 的 `tree.json` 里没有这个节点 —— 树里没有的模块在 start 时直接
  `KeyError`，所以换 profile 必须连树一起换。`routines.yaml` 不写这段，仍走
  tree.json。
- **`Moss` 必须排最后**：passive 一注册就被 kernel 拉起，它按名字 `create`
  ghost/shell/mindflow，那三条路由得先存在。旧写法是整批 `rs.register` 后一次
  push、统一 auto-start，所以 moss 排第一也没事；清单是**逐条注册**，顺序有
  意义 —— 这不是风格问题，是机制差异。
- **`__all__` 即名单，基类不算**：`container.py` 撤下 `Container`。判据不能只
  看"抽象类"：`Routine.__init_subclass__` 会从类名反推 `name`，基类也有名字
  （`Container` → `container`），于是会多出一个不在词表里的 routine。`_collect`
  与 `_collect_pub` 补一条：**没有 name 的不是 routine**。
- **装配参数进 kwargs**：`verbs` / `prompts` 写在清单条目里，`run.py` 不再用
  `type('M', (Moss,), ...)` 造临时子类塞 `is_passive`。参数是数据，不是代码。

**say 只留一个**：删掉 `RealSay`，`Say` 自己按 `SEED_TTS_API_KEY` 选引擎与
播放器（有 key 真声 SeedTTS + 本地扬声器，没有就 silent 零依赖）。判据：
**部署事实不是第二种身份**。两个同名类在清单里只能互相覆盖（谁后注册谁赢），
`__all__` 一次导出两个还会双双注册 —— 让"有没有 key"来决定 routine 的身份，
等于把部署环境写进注册表。

**脑子归装配**：`Moss.run` 造 ghost 前检查 `Ghost.brain_factory is None` 才插
`Brain`（`run.py` 里那行删掉，改成单一出处）。`None` = 还没插，所以测试注入的
假脑不会被覆盖；"一个 Ghost，脑子是外设"这条判据不变，只是插的人从入口换成
装配者。

**验过**：`app/moss.yaml` 载入 = 42 个 routine，名字唯一、无基类混入；用精简
清单（去掉耳朵/键盘/黑板）经 `app.main` 起，`moss up: ghost/shell/mindflow`
全绿，模块树含 `code`。

**只留一个入口（同一轮末尾，用户"删掉吧 只保留一个"）**：`mossv2/run.py` 已删，
moss 只用 `python -m app.main app/moss.yaml` 起。判据不是"少一个文件"，而是
**等价性不能靠人维护**：两个入口各有一份装配顺序、各有一份地址解析、各写一次
`ZERO_KERNEL_ADDR`，只要有一处偏差就是"同一个 moss，两个行为"。这次就撞上了
第一天 —— `run.py` 只把清单路径传给自己的 `load_from_yaml()`，没设进程级的
`loader.set_yaml_path()`，于是 `RoutinesWatcher` 盯的是 `app/routines.yaml`：
改 `moss.yaml` 不重载，而它却把那份清单当自己的账，一触发就会把 app 那套
routine 注进 moss 进程。改通用入口后这类分叉没有落脚点。

同时清掉的地基：`moss.yaml` 头部说明、`ui.py` 连接失败提示（直接告诉你该敲哪条
命令）、`routines.yaml` 的 moss 备注、`test_board`/`test_notify` 跑法里的
`MOSS_HEADLESS=1`（那个开关随 interact 退役早就不存在了）。`app.main` 的
`modules:` 支持保持不变 —— 它现在不是"为第二个入口准备的"，是清单本来就该带
身体拓扑。

## 灵魂不在时，一拍只报一条（2026-10-21）

**现象**：`beat interrupted: routine 14 not found (stopped or invalid target)` 紧接着
`interrupt request failed`，每拍一对，UI 上表现为"打字没反应"。

**事实**：`routine 14` 就是 Ghost 实例（Moss 装配时打出来的 id）。当时那次日志的
内核里查运行表：`root/board/asr/key_listen/moss/routines_watcher/shell/code_kernel/
channel/mindflow/ui_adapter` 都在，**唯独没有 ghost** —— 不是拍坏了，是拍要说话的对象
没了，而桥还举着它的旧 id。

**能复现的一条路**（也是装配件最脆的地方）：热重载 `ghost.py`。watcher 的
`reload` 语义是"停掉该 name 的运行中实例 + passive 自动重启"，可 ghost 的
`shell_id`/`system` 只有 Moss 手里有，空参重启起不来 —— 结果是**老灵魂被停、新灵魂
没生**，mindflow 仍指 14。探针实测：reload 后 `ghost: None`，再发一句必现。

**判据**：**失败之后该做什么，取决于"那个东西还在不在"**。目标已经不在时发
`interrupt` 是给不存在的目标补一刀 —— 它只会把同一件事打成第二条 traceback，读日志
的人会当成两个故障。所以先问一句运行表（`get_running_routines` 按名+id 对号），
不在就**只报一条**（且只报第一次），并把原因推到 UI 的 `interrupt` 主题上：
`灵魂不在了(ghost 14 已停), 需要重启 moss` —— 静默才是最难查的。

**没做**：没有让 mindflow 按名"自动认领"新 ghost（换 id 是重建，不是重连；新实例
是没记忆的灵魂）；也没有让 Moss 常驻监护补种（补出来的是失忆的灵魂，得先定这条）。
更根本的一问留在 ROADMAP：**装配件（ghost/shell/mindflow）不该被裸 reload 打成
"无人认领"** —— 要么 reload 不动被别人组装的实例，要么装配者自己负责重建。

## 耳朵换源：provider 化 + 把 always 策略推给 orch-rs（2026-10-21）

**决定**：`asr` 从「自己开麦 + 直连豆包 SAUC」改成可换的两种耳朵 —— `sauc`（现状，
不删）与 `bus`（只发开收轮命令、只听结果）。常开麦所需的 `always` 策略**在 orch-rs
里补**，不在我们侧补。交接单在 `services/orch-rs/docs/mossv2-always.md`。

**依据**：

- **判停必须有音频的一侧做**。换成总线后麦克风归 micserver、会话归 asrserver，
  我们手里只剩一段文本 —— `listen/stop_judge.py` 那三刀（静音计时/分类器/关键词）
  没有输入可用，硬留在这一侧只会与 orch 的 0.9s 静音抢边界（orch 先收轮，我们的
  定时器被 result 重置，每段白等 1.5s，两句还会被攒成一段）。所以总量一句话：
  **段边界一律由持有音频的一侧给**（manual 由人给、always 由 VAD 给），判停层退位。
- **`manual` 就是 ptt 的天然对应物**：边界=松手这个显式信号；实测 stop→final 310ms。
- **`continuous` 借不来**：它必须先被唤醒（`cont_deadline` 只由 `wake()` 设置），
  且有 3 分钟窗口；而 `always` 要「开机即待命」。
- **provider 而不是替换**：用户要"随时切回来"。`Asr.ear_factory` 本来就是注入点，
  改成按 `AsrInput.provider`（默认读 `MOSS_ASR_PROVIDER`）选工厂，`SaucEar`
  一行不删 —— 切回只是不设环境变量重启。
- **接口面必须拆**：原来一个 `mute(on)` 承担两种语义（人松手 / 我在出声），总线上
  是两条**不同通道**（`orch.cmd stop` / `voice.aec.evt state`）。硬用一个方法就必须靠
  `watchdog` 参数猜主人 ——
  所以拆成 `listen()` / `playback()` / `stop_round()` / `configure(mode)`，
  并新增能力声明 `boundary`（`core` = 自己判停，`clause` = 每条定稿即段尾）。
- **"出声别听我"= 输入闸门，归 orch 自己**（2026-10-21 晚，用户"这个应该是禁用 asr
  不就行了吗" → "aec 已经弃用了…这里怎么有 aec，还引入了业务场景"）：`orch` 手里没有音频
  （`voice.audio.raw` 由 micserver 直发 asrserver），"忽略"在它侧只能是**不开新轮 +
  取消在途轮**（真正让 asrserver 闭嘴的是 `cancel`：它自己也订音频，只有 session 能拦）。
  **不借 `voice.aec.evt`**：aecserver 已弃用（启动脚本只在 kill 列表），借它等于把"我在
  放音"塞进一条为已删功能设计的通道（名字撒谎、语义无主）；orch 里那套 aec 订阅与
  `tts_playing` 字段属死代码，一并删。落法是 `orch.cmd {"action":"mute_input","on":bool}`
  —— "听不听"是 orch 的本职，命令面天然是它的入口，不新增 topic、不新增 state 名。
  在途轮 `abort("muted")`，不是"冻结计数"——asrserver 照样会把喇叭声认成文本并回 `result`。
- **`clause` 边界让交付发生在事件路径上**：`result` → CLAUSE → `_commit()`。松手
  之后 `_flush` 只等结果（≤1.5s），不再 `send_now()` —— 换成返回值上就会与事件队列
  抢时序（那句还没喂进控制层，`send_now` 就 commit 了一个空段）。

**没做**：`SaucEar` 的改名与 `controller.set_boundary()` 三态化还没落地（要等 rs 侧
`always` 可用后一起改，否则只用 ptt 时白改一遍）；多实例共享同一条 `result` 仍未解决
（总线没有 target，调试只能留一个实例）。

**判据回溯**：这条决定推翻的是 2026-10-18「ASR 自实现器官」那句「耳朵是完整器官，
不是别人识别结果的下游」—— 器官边界没变（采集+识别仍是一个整体），只是它现在住在
总线那一侧；`nucleus`/礼仪/申报那一层一行没动。

## 输入与打断：asr 只是众输入之一，总线 `always` 不是 barge-in（2026-10-21）

**决定**：总线 `always` 模式下，每条 `result` 就是**一条普通输入**（一次
`mindflow.declare`），不打断在途回应；是否回应由脑子决定，不由输入侧决定。

**依据**（都在代码里，不是口味）：

- **打断只有一个来源**：`Asr` 的申报出口 `_emit`（`asr/routine.py:397`）把字段
  直通 `mindflow.declare`；打断来自礼仪的 `onset.interrupt=True`
  （`listen/etiquette.py:148`），`declare` 里 `interrupt` 强制升 `FATAL`
  （`mindflow.py:886`），而 `_challenge_incumbent` 第一判据就是
  `interrupt/FATAL → win`（`:548`）。也就是说 **ASR 没有任何特权** —— 它和其它
  输入一样只是一个 `source`，打断是别人（礼仪）替它声明的。
  **作用点是「起话」不是「说完」**：`onset` 每段只发一次（`_first_sent`），交付包是
  `mode='notify'` 挂账不打断。所以"每次说话都打断"准确说是"每次开口都打断"，且只
  在有在途拍时；再加上 `mute_on_say=True`（TTS 出声期关麦），实际打断点集中在
  **"它在想、还没吐第一个字"** 那段 —— 这正是最亏的一种（首字前被打断，两拍一起白等，
  ttfb 8.3s 实测）。
- **起话包开的链是「挂起」的，不思考**：`_chain` 首段 `while not impulse().complete`
  等同 id 尾包（`mindflow.py:690-698`，照抄原版 `wait_ready`）。所以「开口」只换来
  「占了个位置」，反应起点是**判停**（`silence=1.5` / 关键词 / 分类器）而不是第一句说完；
  句间停顿 <1.5s 的部分只是往 `_text` 里累加，走 `heard` 渲染，不再 declare。
- **连续说的真实代价**（总线 `clause` 下最明显，0.9s 就断一句）：每段各有自己的 onset，
  而 onset 带 `interrupt=True` → 每句都会打断**上一句刚起步的拍**。被抢占的两种下场不同：
  还在挂起（没开口）的，残句 `_requeue` 回缓冲、等它自己的尾包配对，不丢；**已经开口**的
  拍，内容只留 `interrupted` 痕，不重新入队。所以表现是"你一直说，它一直在重新起步"，
  而不是"逐句都答一遍" —— 这是"每句都打断"最贵的地方，也是 bus + `always` 建议改用
  `notify`（`interrupt=False`）的实证理由。
- **「作为下一拍进入」是既有机制**，按 `mode` 三档：`default` = 输家回检录缓冲、
  下届独立成链；`notify` = 挂账送达下一拍（`_inject` + `_merge_inject`
  同源拼成一段）；`next` = 挂账 + 插队点灯。另有 `_beat_silent()`：
  在途拍还没开口时普通输入必 `defer` 排队（`:544-552`）—— 「不抢还没想完的脑子」
  这条已经在保护首字延迟。
- **「moss 自主决定是否回应」已经是事实**：ghost 一拍可以一个字都不吐
  （`_beat_spoke` 保持 false，`_moments` 照常收），没有任何门强制 `say`。
  但 `effort` 决定**开不开脑子**：交付包写死 `effort='normal'`
  （`listen/controller.py:382`，注释「交付永远要被回应」）—— 即「想不想说」是
  脑子的事，「要不要想」是礼仪的事，两件事别混。
- **`always` 是听音模式，`etiquette always` 是礼仪**，同名不同层。想要「不打断、
  说完再答」，现成档位是 `notify` 礼仪（`interrupt=False, priority=INFO,
  silence=2.0, deliver.mode='notify'`），不需要新机制、不需要改仲裁。
- **输入密度由边界决定**：`clause` 边界下每条 `result` 即一次 `_commit` = 一次
  交付 = 一条输入。所以 orch 那个 0.9s 静音实际调的是**输入密度**，不是「它多久
  答一次」。忙时 `notify` 会把多条拼进同一拍的 `percepts`（`_merge_inject`），
  所以「连说三句」= 一拍看见三行，而不是三条各自开拍。

**代价（硬矛盾，必须承认）**：不打断 + 半双工闸门 = 它说话时我插的话**丢掉**。
想「插话记着、说完再答」必须不静音 → 就会听见自己的喇叭（真 AEC 才能解决，而
`aecserver` 已弃用）。所以「不打断」在本项目里只能等价于「等它说完我再说」。

**没做**：不改 `etiquette.always` 的 `interrupt`（这是手感决定，不是机制决定，
改它等于换默认对话体感）；也不加「值不值得开脑子」那层判断 —— 现在每句一次推理，
真想省可以后续让核按 rank 决定 `effort`，但那要新机制。

**边界外传**：交接单 §8 写死「总线只管边界与结果，事件里不带 `interrupt` /
`priority` / `reply_required`」，防止 rs 侧顺着 `always` 这个名字往里加业务语义。

**附：文本输入（`user` 源）是「普通输入」的原型**。UI 的输入框走
`ui.send_line` → `declare({'text': line})`（`ui.py:214`），缺省 `source='user'`、
`priority=NOTICE(1)`、`strength=100`、`interrupt=False`、`effort='normal'`、无 `mode`
（`UserInputNucleus.defaults()`）。它**没有礼仪层**：没有起话占坑包、没有静音判停、
不声明打断 —— 所以"不打断、下一拍、由脑子决定答不答"这三件在它身上是天生成立的。
连打两行由 `UserInputNucleus.aggregate=True` 折成一拍（`_fold_source`，与语音靠同 id
配对首尾包是两条路）。**但"不打断"不是硬保证**：attention 强度随时间线性衰减
（`current_strength`），衰减到 100 以下时文本照样能赢 → `_preempt` 停身体 —— 这与
`etiquette.murmur` 注释里记的是同一个陷阱（"preempt 中性化"仍是 mindflow 侧待办）。
另一个既有不对称：语音起话是 `WARNING(2)` + 首包 `strength=150`，比文本的
`NOTICE(1)` + `100` 更容易抢；同一轮里语音赢了的话，同时打出的文本会被
`suppressed` 挂到下一拍，而不是同拍一起看。

## 对齐原版：空拍判据读尾包 + 聚合不许重建 Impulse（2026-10-21）

**原版在哪**（`D:/MOSShell/src/ghoshell_moss`，描述它一律用原版词汇）：

| 原版位置 | 干什么 |
|---|---|
| `core/mindflow/_attention.py:70-71` | `Attention.__init__`：`if not draw_from.complete: thinking_effort='none'` |
| `core/mindflow/_attention.py:129-133` | `wait_ready()`：等第一个 complete 包；abort 也返回，由 `is_aborted()` 区分 |
| `core/mindflow/_mindflow.py:1121-1153` | `_generate_thinking`：首帧折 partial（不思考）→ `wait_ready()` 等尾包 → 折尾包（真思考）→ 回声帧 `while need_observe()` |
| `core/mindflow/_think.py:101-102` | `effort()` = `attention.impulse().thinking_effort` ← **当前包，尾包** |
| `core/mindflow/mindflow_in_shell.py:240-251,274` | 首帧按 `draw_from().interrupt` 停身体；`effort()=='none'` 直接 return（帧已开、已入史） |
| `host/listener/etiquette.py:242-248` | `always` = 打断式轮流对话（`onset.interrupt=True` + `deliver.mode='notify'`），**这就是原版默认** |
| `core/mindflow/listener_nucleus.py:279-338` | onset 包 `complete=False, thinking_effort='none'`；deliver 包 `complete=True`、不设 effort |

**三条对齐结论**：

1. **不完整的包永不思考，是机制而不是约定**。mossv2 原来靠"起话包显式写
   `effort='normal'`"绕过它 —— 换句话说，任何忘了写 effort 的 partial 包都会对着
   半句话开脑子（真 bug 面）。现在 `Attention.__init__` 里补上原版那三行。
2. **空拍判据读当前包（尾包），不读首包**。`record_only = cur.effort == 'none'`
   （`cur = attention.impulse()`），与原版 `_think.effort()` 一字不差。读首包会把
   起话 primitive 的 `none` 传染给回应拍（一句都不答），也会把尾包显式声明的
   `none`（停而不答）漏掉。
3. **聚合不许手写 `Impulse(...)`**。`_agg_source` 原来是白名单重建，`effort` 没列
   → 单条完整输入的 `effort='none'` 静默掉回 `normal`（"停而不答"变成"要答"）。
   改成 `newest.model_copy(update={text, priority, strength})`：只改聚合真正负责的
   三个字段，其余随最新包继承 —— 以后新增字段也不会再被吞。

**没对齐的一处（且不打算对齐）**：原版首帧必产一帧空拍（`_generate_thinking`
一进 `async for attention` 就折帧，`effort='none'` 在 shell 侧直接 return）—— 那是
"thinking queue + async generator" 的结构副产品，效果只是"起话这一刻进史一帧"。
mossv2 没有那个结构，用"挂起"实现同一承诺（partial 不思考），起话的可见性交给
`heard.partial` 渲染 + `interrupted` 痕 + `_requeue` 保残句。要硬补这帧，还得同时
让 onset 包不带 text（原版 onset 的 `messages=[]`），否则半截话会进史 —— 收益不抵风险。

**测试**：`tests/test_dispatch.py::t_effort_from_tail`（首包写 none / 尾包写 none 两种
情形），全量 82 例并行 x8 全绿。

**据此撤回一条建议**：上一轮说"把 `etiquette.always` 的 `interrupt` 改 False" ——
不要改。`always = 打断式轮流对话` 是原版 `host/listener/etiquette.py:242-248` 的
默认坐标（onset `interrupt=True` + deliver `mode='notify'`）；要"不打断"是**换礼仪**
（`notify` / `murmur` / `cue`，原版同一个文件里就有），不是改掉默认那一档的手感。

## 输入渲染对齐原版块形态：`<percepts><listen>` / `<observe>` / `<stop_reason>`（2026-10-21）

**决定**：记忆进脑子那一步从"`[源] 文本` 前缀"改成**原版的块形态**：

```
<percepts>
<listen source="asr" created="D21 22:01:57+8">
今天天气怎么样
</listen>
顺便问下                      ← source=user 的文本输入裸行
</percepts>
<observe>
chan:say: ok
</observe>
<stop_reason>interrupted</stop_reason>
<dynamic_context>
power: 电量80%
</dynamic_context>
<recap>…</recap>
```

**依据（原版行号）**：`<listen>` 是 `ListenerSignal.xml_tag()` 与
`_listen_message`（`listener_nucleus.py:123-125 / 361-370`）的产物，`source` 走属性
（tag 名固定 = 加一个监听源不用改格式），`created` 记**信号到达**墙钟（`message.py:54`
的短格式 `D19 00:01:17+8`）；`<percepts>` / `<dynamic_context>` / `<observe>` /
`<stop_reason>` 分别在 `moment.py:343-349 / :341 / :289` 与 `command.py:799,814`。
而且**只有听来的话带 tag**：原版文本输入（`input_signal_nucleus.py`）没有 `xml_tag`
实现，所以裸文本是对的，不是漏了。

**为什么这不是"加一层包装"**：原版的输入真相是 `Impulse`/`Message` 对象，XML 只是
`Message.as_contents(with_meta=True)` 的输出（`message.py:431-465`），`_adapter`
把它转成 parts 喂模型。我们的对应做法一致 —— **数据层（`history` 条目）保持结构化**
（`role` / `source` / `created`），渲染只在 `Brain._input_items` 一处发生。所以
`recall` 的 JSON、UI、测试的数据面都没变，改的只是"进 prompt 的那一段字"。

**取舍（与原版不一致的地方，都写在注释里）**：

- `created` 只到**拍级**（`Moment.created`，开帧时刻）：我们的 percepts 是拍级拼装，
  `notify` 挂账还会把更早的话并进同一拍 —— 不假装比实际更准。
- `<dynamic_context>` 里用 `源: 文本` 行，不学原版"各源自带 tag"：我们的瞬态 key 是
  任意源名（`power` / `pos`），造 tag 名会碰到非法字符。
- `<recap>` 是我们自创的：原版 `with_epoch_recap` 全库无注册方（那条通路在原件里就没接），
  没有形态可抄；内容仍换全角尖括号（模型自己写的摘要里出现指令标签只会诱发复读）。
- `<percepts>` 把**连续的输入条目合并成一条** user 内容：原版一拍的所有 percepts 本来
  就折在一个 `<percepts>` 里，拍内各源是一起的，不是多条"用户消息"。

**没做**：`<listen low_conf="..">`。原版这个属性要求字级置信度，我们的 `EarEvent` 只有
`kind/text`，`etiquette.low_confidence` 至今没人算 —— 要对齐得先让耳朵吐置信度，那是
另一件事（也顺手记一笔：那个字段现在是配了阈值却无数据的死配置）。

**风险**：模型可能模仿输入 tag 乱写。原版靠提示词约定"输入带 tag、输出走 channel"，
我们的 `prompt/protocol.md` 里那句"routine 的结果以 `[obs]` 回灌"现在**过时了**
（该文件由用户手改，不代改，只报告）。`<chan:routine>` 与 `<listen>` 分属两个命名空间，
实际复读风险低，但提示词对齐前不要当它不存在。

**已对齐（2026-10-21，用户授权代改这一次）**：`protocol.md` 那句 `[obs]` 改成
`<observe>` 块，并新增 `## 输入` 一节 —— 列出 `<percepts>` / `<listen source created>` /
`<observe>` / `<stop_reason>` / `<dynamic_context>` / `<recap>` 六种块，写明**它们是环境给的、
只读**（"不写、不伪造、不复读"，红线也加了一条），以及 `source` 只说明来源、不改变对待方式
（一条输入要不要回应由模型自己判断）—— 后半句就是 asr 无特权那条语义在提示词里的落点。
顺带修掉 `commands/code/example.md:36` 的禁词"动作"（`t_one_noun` 一直是红的，不是这次引入）。

**测试**：`tests/test_brain.py::t_input_items` 重写（块形态 + 属性）；顺手修掉
`t_turn_and_memory` 里那个**既有的红**：假 routine `X` 返回 `{'ok': 1}` 违反"回执只认
`content/details/observe`"的契约（一直产 error 帧，让断言等不到第二条 action 帧），
改成 `{'content': 'ok'}`；`test_dispatch` 的 `t_transient_state` / `t_epoch` 断言跟到
`role='transient'` 与 `<recap>` 块。

## 提示词骨架对齐原版（2026-10-21，用户："提示词应该参考原版吧"）

**原版对位物**：`core/ctml/prompts/v1_0_0.zh.md`（CTML 元指令，固定层，`core/ctml/versions.py`
选版本）＋ `ghosts/dolores/_prompts.py`（人格局：terminology / protocol notice / inception
template）。原版的拼装顺序是 `ctml → project → mode → soul → static`（`contracts/system_prompter.py`
的 `linear()`），`static`（能力面）**永远最后** —— 和我们"稳定段在前、易变段在后"是同一条
前缀缓存纪律，**所以 `app/moss.yaml` 的 `[persona, protocol, capability]` 顺序不改**。

**照搬的骨架**（原版有、我们原来缺）：元指令开场（能并行/实时/有序地驱动身体）→ 三条原则 →
语法细目 → 使用思路（读清单 → 读输入 → 过红线 → 规划）→ 红线 → 最佳实践 → 示例集 →
内联陷阱。其中三条原则按我们的机制改写而**不是删掉**：

| 原版 | 我们 | 差异原因 |
|---|---|---|
| Code as Prompt（`async` 签名即能力） | 签名即能力（`wave(hand, times=2)`） | 能力面渲染的不是 Python 签名，是一行签名式 |
| Time is a First-Class Citizen | 同 | 无差异 |
| Structured Concurrency（FIFO occupy / 兄弟并行） | 结构化并发（同轨先后、分轨同时） | 轨是平的，没有通道树与 occupy 传播 |

**刻意没搬的（机制不存在，搬了就是教错）**：

- `__main__` 根通道 / 通道树 / 父子阻塞（occupy 传播）、`<channel><interface>` 表面 —— 我们是
  扁平轨 + 每行一个签名；"先发子通道"那条推论在这里没有对位物。
- 装饰器 `@nonblocking` / `@observe` / `@macro` —— 我们的 observe 是**调用处属性** ＋ routine
  自己的 meta，宏与 nonblocking 不在词表里。
- `text__` / `chunks__` / `ctml__` 三个保留参数名 —— 我们统一叫 **body**（`meta['body']`）。
- `_cid` 身份标签 —— 回执信封里有 rid，但模型不指定、也对不上，写出来只会诱发幻觉。
- 参数类型后缀 `arg:str=` / `_args` 位置参数 / CDATA —— `body.py` 的属性正则只收字符串
  （`_ATTR`），数组类参数靠 routine 自己解析（如 `poly` 的 `points="x,y x,y"`）。
- "通过 `moss_interpret(ctml=...)` 这个工具发出" —— 原版走函数调用，我们**裸文本就是输出**，
  所以开场那句必须写"身体边收边执行"，不能提工具。

**移植过来的两处人格局**（`persona.md`）：**只说出该被听见的话**（路径/id/公式/表格/代码别念，
交给黑板或文字 —— 原版 voice-first 的"never speak what is not meant for ears"）与
**安静也是回答**（原版"a moment needs no reaction, noop it"；我们一拍本来就可以零输出）。

**用语必须翻译**：原版通篇写"命令"，我们的 `t_one_noun` 禁 `动作/命令/指令` —— 借骨架时逐句
改成 routine，"首动作提速"也只能写成"先发最轻的"。

**顺手补了能力面的机制通告**：原版把"这个 routine 要 body / 出厂 observe"印在签名上方的
装饰器里（`@observe`）；我们一行一个签名，原来只在 `⟨body: …⟩` 标了 body，**observe 完全没标**
—— 于是 `query_weather` 的"结果会自动回到眼前"只写在 description 里，模型分不出那是机制保证
还是作者客气。现在 `_sig_line` 按 `meta['observe']` 追加 `⟨observe⟩`（`tests/test_prompt.py::
t_observe_marker` 守住：声明的印、没声明的不印）。

**两处原版说法原本在我们这儿是假的，现在是真的是因为把它做实了**：原版说"流式参数当属性传 →
错误"，语气是**当场被拒**。我们原来完全交给 pydantic（默认 `extra='ignore'`），于是
"参数名写错"= 静默失效：不报错、不生效，模型以为做了，然后自己编一个结果。

**决定：投递口当场拒（2026-10-21，用户："那我们也当场拒绝"）**：

| 写错 | 现在 | 落点 |
|---|---|---|
| 参数名不在签名里 | `error(x): x 没有参数 bogus(它收: hand, times)` | `shell._check_args`（catalog 的 `input_schema.properties` 白名单） |
| 给没声明 `⟨body⟩` 的 routine 塞内容 | `error(x): x 不收 body … 这段没人读`，字节不投递 | `shell._deliver_body` |
| 代码面 `call('sleep', dur=1)` | 当场抛 `CallError`，交给这一格处理 | `code_kernel._check_kwargs` |

三条边界写清了才敢拒：① **只拒 catalog 里查得到名字的**（热挂 / 扫不到 = 不知道，不拦）；
② 白名单与模特看到的签名**同源**：`input_schema.properties`。容器原来没有 schema，`timeout` /
`times` 是暗参数（用了会被拒、可清单上从没印过），所以给 `Container` / `Loop` 补了
`ContainerInput` / `LoopInput` —— 顺带让能力清单印出 `_(timeout) ⟨body: …⟩`；
③ body 只能在**字节真的到了**的时候拒 —— `<x/>` 与 `<x></x>` 是同一条路（`body.py`），开标签
时刻还看不出有没有内容，所以实例已建、照常跑完，只是那几段字节不投递 + 报一串。

**为什么值得从"静默"改成"报错"**：静默错最贵 —— 模型写下 `<say text="你好"/>` 会以为说了，
下一拍继续按"已经说了"推理；报错则带可用参数名，下一拍就能改对。代价是白名单要跟 catalog
同源（`_rescan_meta` 已覆盖词表热变，`reg/unreg` 之后自动重算）。

---

## 保留字符：实体还原 + CDATA 保护区（2026-10-21）

**起因**：对位原版 `core/ctml/token_parser.py`，这两处不是"少一种写法"，是"某些内容写不出来"：

| 缺 | 原版 | 我们原来 |
|---|---|---|
| 实体 | SAX 还原 `&lt;` `&quot;` | 字面传下去（值里写不了引号） |
| CDATA | SAX 支持 | 顶层 `Malformed`；body 里标记原样泄漏（`say` 会念出来） |

顺带一个真 bug：顶层孤立 `<`（`1 < 2 对吧`）会把后面整段吞进缓冲，流尾只报
`unterminated tag` —— 错误指向"标签没闭合"，而模型写的是小于号。同类病：错要指向
真地方，否则下一拍它会去改一个根本不存在的标签。

**三条落点**（全在 `body.py`，纯词法层）：

1. `_unescape`：只认 XML 五个预定义实体 + 数字实体（`&#65;` / `&#x42;`），**必须带分号**；
   认不出的原样返回 —— 单写 `&` 太常见，宽松到无分号会误伤 `R&D` / `a &lt b`。
   用在两处：属性值（`_parse_tag`）与**本层文本**（`_drain_top`）。
2. `_cdata`：`<![CDATA[ … ]]>` 标记剥掉、内容字面（**不**还原实体）；顶层产出 `Text`，
   body 内产出 `ChildBody`。CDATA 里的 `</say>` 不算闭合 —— 这是它唯一存在的理由。
   两个"等在边界上"的补丁：`_cdata_partial`（`<![CDA` 还没收完不能判成文本）、
   `_entity_tail`（`&#65` 到了而 `;` 在下一片，先吐出去就再也拼不回来）。
3. `_looks_like_tag`：`<` 后是字母/下划线/`/` 才等标签，否则当场当字面量（`1 < 2`、
   `a < b` 不再吞文本）；`close()` 时半截标签**当文本吐出**再置 `unclosed`（原来直接丢，
   于是"内容不丢"只在正常路径成立 —— 而流断的时刻最需要留下证据）。

**body 的实体：分两路（2026-10-22 修订，见下一节）**：原先一律不解码，理由是"`ChildBody`
是给下一层的源码，父若解码，`<all>&lt;left:x/&gt;</all>` 里的转义就变成真标签执行"。
这条只对**容器的 body** 成立 —— 它确实是下一层的标签源码。对叶子（`say` 的话 / `code` 的
源码 / 黑板要写的字）不成立：那些字节到此为止，没有谁再把它当 CTML 读。实测代价很硬：
`<code>` 里写 `if abs(x-0.5) &lt;= 0.48:`，字面 `&lt;` 进 Python，当场 `SyntaxError`。
规则因此收敛成"**实体只在本层被消费时才还原**"：属性、本层文本、**叶子 body** 还原；
**容器 body** 不还原。

**已验证的自然写法**：`<say>今天 1 < 2 呢?</say>` 直接可用 —— body 拼回来完整
（`今天 1 ` + `<` + ` 2 呢?`）、闭合正常。因为 `_looks_like_tag` 在 child 模式也生效，
`< ` 不会被 `_find_tag_end` 一直找到 `</say>` 的 `>` 上去（那会把闭合一起吞掉）。

**CDATA 的边界**：它保护**内容**，不保护容器 body 里的标签 ——
`<all><![CDATA[<x/>]]></all>` 里的 `<x/>` 剥壳后仍会被容器当 CTML 解释（容器要把
body 当源码重扫，剥壳那层分不出"这段原本是字面"）。提示词照实写这条，不假装能保护。

**测试**：`test_body` 8 → 11（`t_entities` / `t_cdata` / `t_less_than`，后者覆盖分片
边界上的单个 `<`）；`t_malformed` 的 `a<3> b` 仍走 `Malformed`（有 `>` 时看起来就是
标签，不进字面分支）。全量回归：`test_dispatch` 85 例 x8、`test_prompt` 6、
`test_brain` 6、`test_board` 7 全绿。

---

## 叶子 body 还原实体，容器 body 不还原（2026-10-22）

**触发**：实测一次 `<code>` 调用 —— 模型写的是 `if abs(x-0.5) &lt;= 0.48 and ...`，
`&lt;` 字面进了 Python，`SyntaxError (line 10)`。这不是模型的错：它在标记语言里转义
是本能，而 body 是**源码**，整条链按字节原样透传，谁都没还原。

**判据**：body 分两种身份，看它是不是"到此为止"。

| body 身份 | 谁 | 还原实体 | 为什么 |
|---|---|---|---|
| 叶子（内容到底了） | `say` 的话、`code` 的源码、`bb_write` 的字、`bb_pixel` 的 JSON | **是** | 没人再把它当 CTML 读；不还原 = 字面进 Python/引擎 |
| 容器（下一层的源码） | `_` `all` `any` `loop` | **否** | 父一还原，`&lt;x/&gt;` 就变成真执行（注入） |

判据落在 `shell._route` 的返回值 `body_raw = verb in _body_verbs and verb not in
CONTAINERS` —— 与"哪些名字有 body""哪些是容器"同源，没有第三处名单。

**落点**（两处转发口，各带一个尾巴）：

- `body.decode_body(text, tail)` —— 还原 + 扣住片边界上的半截实体（`&#65` 到了而 `;`
  在下一片，扣住；流尾由 `_close` 照原样吐出）。与 `_drain_top` 里 `_entity_tail` 同一
  套判据，不是新写的。
- `shell._deliver_body` / `container._events` 的 `ChildBody` 分叉；两边的 `_close` 冲尾。

`容器.body_text` 保持**原样**（loop 重放靠它重扫），所以结构层字节永远不解码 —— 只在
"投给叶子实例的那一刻"还原。这一条让重放与首跑走同一条路。

**顺带的好处**：提示词现在可以主动推荐 `&lt;` 而不是只警告"小心" —— `a<b` 会被当成
标签开头一直等到 `>`，`a&lt;b` 绝无歧义且还原后就是 `a<b`。这是"body 里的 `<` 一律
写实体"的正当理由，不再是一句没依据的劝告。`code/example.md` 的"注意"也补了这一句。

**边界**：`&amp;lt;` 只解一层（得 `&lt;`，不递归），所以字面 `&lt;` 仍然写得出来。
`R&D` 不变（无分号不认）。单写的 `&` 不变。

**测试**：`test_dispatch` 86 例 x8 全绿，新增 `t_leaf_body_decoded`（根上的 code 与
容器里的 code 都要拿到真 `<`；`<all>&lt;x/&gt;</all>` 的 `x` **不能**被执行 —— 那是注入）。
`test_body` 11、`test_prompt` 6 不变。

---

## kernel 只结算自己的投影，实例的停归 hub（2026-10-22）

**触发**：退出日志里 `stop ui_adapter-16: no lifecycle.stopped in 2.0s, giving up
(host 'moss-ui' did not confirm)`。moss-ui 是独立进程拨入的 hub —— kernel 在等一个
本来就不该等的人。用户随即问：kernel 和 hub 是两个独立体系，跨进程时是不是该在
hub 侧清理？

**判据**：这件事得拆成两件，混在一起就必然越权。

| | 谁做 | 凭什么 |
|---|---|---|
| **停实例**（跑 stop hook、cancel main task） | **hub** | 实例在它的内存里，kernel 没有执行它的手段，只有"请求" |
| **清投影**（nodes 表、`cmd:` body task、routes、在途调用） | **kernel** | 那些是 kernel 自己的账，它不死就得自己收 |

"请求 + 等确认"只在**通道可靠**时成立，所以分界线是 `KernelConn.local`，不是"conn
在不在"：

- **同进程 hub**（inproc link）：通道是内存队列，回报必达；而且**全局树只在这一侧**，
  先子后父只有 kernel 能编排 → 发 `lifecycle.stop`、等 `stopped`、再摘投影。
- **跨进程 hub**（grpc peer）：确认会随断链一起消失；对端本来就有一套完整自清
  （transport 断 → `hub.handle_peer_down` → `runtime.peer_down_all` +
  `lifecycle.stop_all_local`，stop hook 与 main task 照跑，只是不回报死者）。
  → kernel 只 `_settle_local`：解 future、摘节点、还模块、中转父，**不发 stop、不等确认**。
  断链本身就是通告，不需要额外协议。

**上一版错在哪**：`Kernel.stop()` 扫残余时对每个节点都用 `_stop_node`，而它只判
"conn 在不在"。conn 在 = 发 stop 等确认，于是独立进程的 hub 也被逐个数账，等满耐心
变成假 WARNING。假红每次都出现，真卡住的那个就被淹了。

**落点**：

- `KernelConn.local`（`Kernel.bind` 置 True，grpc 侧默认 False）；
- `_stop_node` 三分支：失联 → settle；非 local → settle + `await cmd.wait()`（等的是
  kernel 自己的 task 退出，不是等对端确认，否则 pending task 照样漂到循环关闭）；
- `Kernel.stop()` / `Connection.stop()` 的 docstring 写清"等与不等按宿主分"。

**测试**：`routine-py/tests/test_kernel_stop.py` 两个 case —— 跨进程 hub 的节点
**不能**收到 `lifecycle.stop`；同进程 hub 的节点必须先收到 stop、回报前不许摘投影。

**边界**：inproc 下"先子后父"仍然只在 kernel 这一侧成立（hub 的 `stop_all_local` 是
并行 gather，不保序），所以那条路的请求-确认不能省。

**跨语言落点（同日对齐，py 是基准）**：

- **ts**（唯一有 kernel 的语言）：`KernelConn.local` / `stop()` 扫残余并点名 / `stopNode`
  三分支 + 新 `settleLocal` / `dropConn` 改走停路径（原来就地摘，跨 hub 父子会成孤儿）/
  `Connection.stop()` 顺序改 kernel → grpc → link / `SHUTDOWN_TIMEOUT = 3.5`（`Command.stop`
  收一个可选耐心）。验证：`tsc --noEmit` 通过 + 临时脚本两个 case（peer 不许收
  `lifecycle.stop`；local 必须先收到 stop、回报前不许摘）。
- **go**（只有 hub，没有 kernel）：两处偏差已修。`ForceStopPeer` 改并行 + join；
  `forceStopOne` 补 `Stop()` hook（有界）—— 原来直接 `OnStopped` + cleanup 是硬杀，
  断链时最需要的业务收尾（收麦/断服务/flush）整个跳过。新增 `OnPeerDownAndWait`，
  client 重连循环改用它 —— 原来是 `Runtime.Go` fire-and-forget，销毁期与重建期重叠会撞 rid。
  `go build ./...` 通过。
- **rs**（只有 `RemoteKernel`）：hub 侧 `stop_all_local(peer_id)` 已经是 graceful（`stop_instance`
  里 stop hook + 超时）且重连循环里 `await` 它，语义与 py 一致，未改。

---

## 退出路径上的日志不许是 WARNING（2026-10-22 晚）

**触发**：每次 Ctrl+C 都出现

```
收到 Ctrl+C, 正在关停 (再按一次强制退出)...
WARNING moss.asr - asr ear stopped; 待命(原因见上一条 state 日志)
关停完成 (4.0s)
```

**三件事，分开判**：

1. **那条 WARNING 是假的。** `asr.stop()` 关耳朵 → `ear.events()` 结束 → `run` 里
   "耳朵自己收摊"的分支。判据是 `_halt.is_set()`（`stop()` 先点灯再关耳朵）：
   点灯 = 关停路径，打 INFO；**没点灯**才是耳朵自己掉了，那时候"原因见上一条
   state 日志"才真的存在。混在一条里，每次退出必刷一条假红，真故障就被淹。
2. **`4.0s` 里 2.0s 是固定的 grpc grace。** `server.stop(2.0)` 等 dial-in peer
   自己关流 —— peer 是常驻重连的，永远不会关，所以每次都吃满。py 实测（UI 拨入
   + 真麦克风）：`kernel.stop 0.07s` + `grpc.stop 0.51s` = **0.6s**。
3. **退出语境不该陪对端 2s。** `GrpcKernelServer.stop(grace=...)` 收参数，
   `Connection.stop` 传 **0.5**（ts `stop(graceMs=500)`）。理由：对端反正要重连，
   这段等待只让 Ctrl+C 更慢。go 侧本来就是 `server.Stop()` 硬停，语义一致。

**顺带**：`_stop_node` 停在 ≥0.5s 时打一行 `⌛ X 停用了 X.Xs (host ...)`。
"关停完成 (4.0s)" 若没有名字，只能靠猜 —— 耗时几乎全是某一个 routine 的
stop hook 在等，而常驻 routine 的关停本该是毫秒级。

**回归**：`test_kernel_stop` 两 case、`test_owner_gone` / `test_hook_fault` /
`test_start_priority`、`test_body 11` / `test_ctml 5` / `test_prompt` /
`test_brain` / `test_board` / `test_frames` 全绿；ts `tsc --noEmit` +
`tests/kernel_stop.ts ALL PASS`。

---

## 别家的标记是词法错误，不是旁白（2026-10-22）

**触发**：线上模型输出

```
Moss: <｜｜DSML｜｜ calls>
      <｜｜DSML｜｜ invoke name="noop"/>
```

用户问“这什么雷霆语法”。DSML = DeepSeek 原生的函数调用标记（special token），
与我们无关；但它**被当成模型说的话念了出来**，这一步是我们的 bug。

**两层要分开**：

1. **模型用错了协议**：它想执行 `noop`，却走了训练里的 function-call 格式而不是
   CTML。这种事靠提示词治不彻底（`<|im_start|>`、`<｜…｜>` 都是同一类先验），
   正确做法是让它**当场看见**自己的格式错了 —— 错误自带债，下一拍能纠。
2. **我们把它当旁白**：这才是要修的。判据本来是 `_looks_like_tag`：`<` 后不是
   标签名字符就当字面量。`｜`（全角竖线）不是名字字符，于是整段进了 `Text`，
   裸文本 = 旁白 = final answer。

**根因是分片相关的两种结论**（实测钉住）：

| 到达方式 | 结果 |
|---|---|
| 整块 `<｜｜DSML｜｜ invoke name="noop"/>` | `Malformed`（对：`_parse_tag` 判死） |
| 逐字/小片流式（线上真实形态） | 一堆 `Text`（错：成了它说的话） |

同一段字节不许有两种结论 —— 这是比"哪种结论更好"更硬的要求。

**修法（只有一条）**：`_looks_like_tag` 把 `|` / `｜` 也算作"等 `>`"的首字符。
等到 `>` 之后 `_parse_tag` 自然判死，走既有 `Malformed` 通道；逐字与整块从此
同结论。

**不加定向提示**（用户拍板："这种就是未知 routine，obs 里报错即可"）。理由是把
它归到既有那一类去 —— 与 `unknown verb` 同构的一条错误帧 + 债，下一拍模型自己
改；为一个别人的标记在词法层写解释，等于承认它是个需要认识的概念。词法层不认识
任何 routine 名字，也不该认识任何别家的标记语言。

**代价（明写）**：`<` 后**紧邻**竖线的正文会等到流尾。`x < |y|` 中间有空格，
走不到这条路；`x <|y|`（无空格）这种写法会在 `close()` 里原样吐出（内容不丢），
但会置 `unclosed`。可接受的误伤面。

**测试**：`test_body.t_foreign_markup` —— 两种外部标记 × 分片 1/3/1000 全部
`Malformed`（钉"分片无关"，这是唯一必须守住的事）；`t_less_than` 继续守 `1 < 2`
与 `x < b`；无 `>` 的竖线正文测内容不丢。

## 没有音频可传就不占线：always 描述的是麦克风，不是连接（2026-10-23）

用户先问"ptt 的 asr 音频上传常开? 这样太费钱了"，我答"是的"；随后他指出**建连
与上传不是一回事** —— 对，是我把两件事捆在一起说了。分开看，耳朵身上有三层：

| 层 | 做了什么 | 花钱吗 |
|---|---|---|
| 建连 | `websockets.connect` 握手 + `ping_interval=20` 心跳帧（无音频） | 不花。按音频时长计费的方案轮不到它 |
| 会话 | `init` 后服务端维持上下文；超时判据是"8.5s 收不到**音频包**"（`45000081`） | 不单独收费 |
| 上传 | 每 200ms 一包 `build_audio`，队列空就补 `_SILENCE` | **这里才是"上传"，而且 ptt 没按时一直在发生** |

顺带纠正我同一条答复里算错的数：流量不是 115 MB/h。`build_audio` 走 gzip，6400
字节全零块压到几十字节，空闲约 0.3 KB/s ≈ 1 MB/h。**流量从来不是问题，问题是
静音也按 200ms/包 折算成识别时长交上去了。**

**还有一处更脏的**：TTS 闸门（`_on_output`）与 ptt 共用同一个 `_muted` 开关，
后写覆盖先写。松手后它一出声，说话结束那条 `mute(False)` 就把"没按"的静音解掉
了 —— 麦克风从此常开，整间屋子都在上传。这是"按下才上传"真正的破口。

**修法：没有音频可传就收流**（判据三条，一条路）：

- **ptt 没按**（`_muted` + `watchdog=False`）；
- **闸门静音**（TTS 出声期，麦克风在丢帧）—— 原来是补静音保会话，改成收流；
- **always 里安静到 `_SILENCE_CLOSE_S=3s`**（长于服务端 VAD 800ms 与礼仪判停
  最宽的 2.0s；短了会把还在判停的那段剪断，后半句另起一段、前缀全丢）。

落地成两件东西：

- `_dormant`：`_muted` 或"距上次过能量门已 > 3s"；
- `_push` 的能量门 `_GATE_DB=-50dB`：**没开口时的静音不入队**，开口之后
  （`_streaming`）静音照传 —— 服务端要靠那段尾巴判句尾。常开麦的空闲期
  只剩每 200ms 一次 rms 计算，线上零字节、也不建连接。

**依据（原版 `D:\MOSShell`）**：

- `contracts/asr.py:silence_gate_factory(threshold_db=-50.0)` —— 默认门控就是
  `rms_db < -50` 丢帧；
- `volcengine_sauc/recognizer.py:_next_gated_chunk` —— 门控**拦在 `init` 之前**：
  返回 None 就不开 WS、不 init；一旦放行，本 segment 内不再拦；
- 同一个文件类的 docstring：**"一条 WS = 一次说话(turn) = 一个 segment"**、
  **"WS 是 turn 的实现细节，不是流的生命周期"**；收尾靠 `commit()` 发负序号
  拿尾包，不是靠静音保活。

也就是说，我们在 ptt 上"修"出来的形态，正是原版 always 的形态：WS 跟着
"这一段要不要传"走，而不是跟着"进程活着"走。**"always"描述的是麦克风，不是
连接** —— 这一点我们两条模式原来都搞错了。

**收流的空窗（`_TAIL_S=0.6s`）不能省**：最后 200ms 音频的回包还在路上，
一松手就 close 会把最后几个字剪掉 —— 这 0.6s 里线上一包都没有，不产生时长。

**`events()`**：`_wait_awake()` 卡住等"有人开口"（过能量门的块）才建流；
`mute(False)` 只解麦克风，不再等于"开流"。**`_on_output`**：ptt 没按住时闸门
**既不必关也不许开**，直接 return。

**代价（明写）**：每段一句一条流。按下/开口那一刻多一次握手 200~400ms，音频
在队列里等着**一个块都不丢**，只是首个 partial 晚到一点；松手交付走的是已经
攒下的 partial（`send_now`），不等服务端 final —— 原来也不等（`_listening()`
已经关了门，迟到的 final 本来就被丢）。跨句上下文随流复位，原版每 turn 一条
流本来就不靠它（它靠 init 的 corpus/热词补）。

**没定的一件事**：静音时长到底算不算钱，取决于计费口径（资源 id 是
`sauc.duration`，但走的是 Agent Plan 套餐通道）。对账办法：`stats['silence']`
一个计数 = 200ms，挂机一小时 ~18000，拿静默时段账单增量比。**改完之后两种模式
的这个计数都应该趋近 0 —— 它从"保活量"变成了"静音上传计量表"。**

**测试**：`test_listen.t_ear_upload_on_demand`（发送泵三条路：段内补静音 /
闸门静音收流 / 松开收流 + 待命唤醒 + 闸门不许替 ptt 开麦）、
`test_listen.t_ear_always_gate`（能量门：常开麦的静音不入队、开口才叫醒、
段内尾巴照传、安静够久该收流）、`test_listen.t_ear_session_on_demand`
（整套 `events()`：没按不建流、按了不出声也不建、开口才建、松手 0.6s 收流、
待命期不重连、再按重建）。后两个用假 `websockets.connect` + 假 `_Mic`。

## 判停即收流：commit 发负序号尾包（2026-10-23 晚，用户"可以"）

上一节留下的最后一步：判停之后为什么还要等 3s 静音超时？答案是**不需要** ——
段边界一旦由策略层落定（礼仪的 silence/keywords/classifier，或 ptt 松手的
`send_now`），这一段文本就固定了，再传也没有字会进交付。原版 `Recognizer.commit()`
就是这一手：

```python
def commit(self) -> None:
    """结束当前 segment: 让 send loop 发 NEG, 拿尾包后切段开下一 turn."""
    self._commit_event.set()
```

**接线只有一处**：`Asr._witness(text, final=True)`（礼仪 `_commit` 的旁观针，
ptt 松手与 always 判停都经过它）→ `ear.commit()` → 发送泵发
`build_audio(b'', seq, is_last=True)`（负序号 = 本流最后一块）。协议侧早就支持
（`sauc.build_audio` 的 `is_last`），缺的一直是"谁来调"。

**三条出口，按优先级**（`Ear._send`）：

1. **判停**（`commit()`）：发负序号尾包就返回，**不收流** —— 尾包还在路上；
2. **待命**（`_dormant`）：一个字节都不发，空等 `_TAIL_S=0.6s` 再 close；
3. **段内**：有块发块，没块补静音（服务端按包推进时间轴，断口会被当成句尾）。

**判停靠「哨兵入队」唤醒，不靠轮询**（用户追："为什么不用事件而是轮询?"）。
第一版是置 `_commit` 事件 + 发送泵在待命期 200ms 超时回来看一眼 —— 那等于
**有事件却靠轮询探**，代价是尾包最迟晚一个周期才发。问题在于发送泵睡在
`q.get()` 里，它只看队列，事件单独叫不醒它。

修法：`commit()` 除了置事件，再往队列塞一个 `_FINAL` 哨兵（队列满就丢最旧腾
一格）。唤醒源和数据走同一条路 —— 零轮询、零新事件，也不必碰
`asyncio.wait([q.get(), event.wait()])` 那套的 `Queue.get()` 取消归属问题。

连带三处：

- 待命期的空窗改成**一次等到点**（`_TAIL_S` 剩余时间），不再 200ms 轮询；下界
  留 1ms，因为 `wait_for(timeout<=0)` 不看队列直接判超时，会把已躺在队列里的
  哨兵/新块当成"空窗已到"。
- 发尾包前**排到哨兵为止**：哨兵之前的块属于已判停那段，跨流共享队列会让新流
  把它们当成新段开头再识别一遍；哨兵之后保留 —— 判停落定那几毫秒里入队的可能
  已经是下一句。
- 测试 `t_ear_commit` 加了时序断言：`commit()` 到尾包必须 < 80ms。轮询实现下
  这里至少 200ms，正是上一版白扔掉的那一截段尾延迟。

**两处必须一起改，否则是新增 bug 而不是优化：**

- **判停之后这条流吐的文本一律不进策略层**（`_commit_sent_at` 那道闸）。尾包里
  的定稿若被 `_on_event` 喂进 controller，会 `_ensure_segment` 开一个新段、
  发一条新起话包，**同一句交付两遍**。判停是"这段结束了"的语义承诺，尾部文本
  属于已交付的那一段。
- **`_end_segment()` 清 `_last_sound`**。判停时距上次出声可能只有 1.5s（礼仪判停
  最短那档），不清的话 `_dormant` 还是 False —— `events()` 会立刻重连一条新流，
  接着把静谧的 1.5s 当静音包传上去，白建一条流、白传一段静音。清掉之后判据只剩
  一条：**有新声音才建流**。

**收尾耐心 `_COMMIT_GRACE_S=2.0`**：发了尾包服务端却不回，到点直接收流（原版
`tail_grace_seconds` 同一个意思），不许卡在 recv 里等。

**省掉的量**：always 下从判停（1.5s 静音）到原来 3s 收流判据之间的那 1.5s，
加上 0.6s 空窗 —— 每句话 2s 左右的静音上传归零；段尾的识别延迟也从 3.6s 降到
尾包往返（通常 100~300ms）。ptt 下松手交付的路径同样提前收流。

**一个顺带的语义修正**：`mute(False)`（按下）只解麦克风，**不建流** —— 建连也
只为传音频，真有声音（过能量门）才建。所以 ptt 的 `asr stream up` 现在出现在
"说了第一个字"那一刻，而不是"按下"那一刻。代价是首个 partial 晚 200~400ms
（握手），音频在队列里一个块都不丢。

**测试**：`test_listen.t_ear_commit`（段内判停发负序号但不收流 / 待命期判停
也发尾包 / 判停后这条流的定稿不进策略层）。`t_ear_upload_on_demand` 的待命
用例同步改成"按下不出声也不建流、开口才建"。

## 松开前几个字识别不到（2026-10-23 晚，用户实测）

症状稳定复现：ptt 说完松手，最后几个字总是丢。两个产地，一硬一软。

**产地一（结构性，每句必丢）**：采集粒度就是上传粒度 ——
`sd.InputStream(blocksize=200ms)`，一次回调一整块 200ms。松手 → `set_mute(True)`
立刻生效 → 松手那一刻**正在采**的那 200ms 在下一个回调里被整块 `return` 掉。
所以每句话结尾固定丢最多 200ms，正是"几个字"的量级。

**产地二（时序）**：`_flush` 固定 `sleep(0.15)` 就 `send_now`。最后一块音频要
上传、服务端要吐对应 partial（通常 100~300ms），0.15s 常常还没回来就交段了；
而 `send_now` 之后 `_listening()` 关闸，晚到的 partial 一律被丢。

**修法（两个都对上）**：

- **采集粒度与上传粒度拆开**：`_ACCUM_MS=40`，`_cb` 收 40ms 一块，攒满
  200ms 才交给事件循环（上传包大小不变，协议不动）。再给 `InputStream` 加
  `latency='low'`，少一层驱动缓冲。
- **静音边界不许整块丢**：`_feed` 里若「这一块的起点 ≤ 静音起点 + 一个采集
  粒度」，说明它横跨松手时刻 —— **立刻整体交出**（哪怕只有 40ms、没凑满 200ms）。
  边界损失从 200ms 降到 40ms。
- **`_flush` 改成等安静**：至少 0.2s；若最近 0.2s 还有新 partial 就继续等
  （"还在吐字就再等等"）；上限 0.5s。上限必须小于耳朵收流前的空窗
  `_TAIL_S=0.6` —— 松手已关麦，那条流 0.6s 后自己收掉，等得比它久就什么
  都等不到了。

**没解决的一层（明写）**：服务端尾包的 `final` 比 partial 全，但它到达时交付
已经发生（`_flush` → `send_now` → 判停 → commit → 尾包）。要真用它，得改成
原版那套「先 commit 发尾包 → 等 final → 拿 final 交付」，属于改造交付链路，
不是这次的范围。当前押的是「partial 在窗口内到齐」。

**测试**：`test_listen.t_mic_edge_block`（攒块 5×40ms 成一包 / 横跨静音起点的
那一块立刻送出 / 边界之后零采集）。

## 一直在刷 stream up/down（2026-10-24，用户贴日志）

现象：`asr stream down (安静 3s)` 与 `asr stream up` 交替出现，间隔 2~20s。

**产地：开门门太松。** 上一版把「入队门」（`_GATE_DB=-50`，原版
`silence_gate_factory` 同一个值）同时当成了「开门/续命门」。环境底噪（风扇、
空调、键鼠）常年就在 -50 上下浮动，于是：

1. 一个块过 -50 → `_last_sound` 刷新 + 建流；
2. 段内每个过 -50 的块都给它续命 → 流被底噪吊着，可能开 20s；
3. 底噪换成 3s 低谷 → 收流；
4. 再过一个块 → 又建流。

一次循环 = 一次握手 + init + 最多 3s 静音上传，日志上就是 endless 的 up/down。

**判据拆成两个门，各管一件事：**

| 门 | 值 | 管什么 |
|---|---|---|
| `_GATE_DB` | -50 | 入队：低于它就不传（原版同值，不动） |
| `_OPEN_DB` | -42 | 开门 + 续命：只有像人声的块才建流、才给流续命 |
| `_OPEN_BLOCKS` | 2（400ms） | 连续几块才认：单块咔哒（键鼠/碰麦）不开门 |

那 400ms 候选音频攒在 `_pending`（≤2 块），开门时一起补进队列 —— **第一个字
不丢**；底噪永远攒不满 2 块（`_open_run` 一断就归零），永远开不了门。

**连带三处：**

- `_dormant` 里的「安静」改成按 `_OPEN_DB` 算 —— 底噪既开不了门，也别想给
  已有的流续命；
- `mute(True)` / `_end_segment()` 清 `_pending` 与 `_open_run` —— 关麦前的
  候选块不许当新一段的开头；
- `stats['duds']`：一条流一个字符都没吐出来，就是一次「空转」（开门多半是
  噪声）。这是把「到底在建什么流」变成可查的数，不靠猜。

**日志带 dB**：`asr stream up (-45 dBFS, wss://...)` —— -45 左右说明底噪还在
过门（要继续抬 `_OPEN_DB` 或改成自适应），-20 左右才是真有人在说话。`status`
里也多一个 `db`（最近一块的 dbFS）。

**测试**：`test_listen.t_ear_always_gate` 扩成四条断言（底噪不入队也不开门 /
单块不开门 / 两块开门且补上攒着的两块 / 段内尾巴照传）；两个 events 用例的
「开口」步骤同步改成推两块。

## 车身挂上来：car routine 系列进 moss 装配（2026-10-24，用户"把 car routine 系列配置上来"）

小车已经上电（底盘 systemd 自启 + rosbridge 就绪），把 `car/*` 挂进 mossv2。
三件事必须一起做，少一件就是"注册了但叫不动"。

**① 词法要认斜杠。** `capability` 是按**注册名原样**印清单行的，模型照抄那
一行去写标签 —— 而 car 的名字里就有 `/`（`car/move`）。`_TAG_NAME` 原来只认
字母数字下划线连字符点，于是清单里那一行名字**永远写不成标签**，报出来还是
`malformed tag`（看着像模型写错了，其实是词法自己不认识自己的清单）。
`:` 不能借用：它已经是 `chan:cmd`（轨道前缀），`<car:move/>` 会被当成"投到
car 这条轨上的 move"，那是个更坏的错。所以斜杠进字符集：

```
_TAG_NAME = r'[A-Za-z_][\w\-./]*(?::[A-Za-z_][\w\-./]*)?'
```

自闭合不受影响（`inner.endswith('/')` 先剥再匹配），`</car/navigate/manage>`
闭合照裸名配对。判据一句话：**名字是事实，词法跟着名字走**，不是反过来让
清单去迁就词法。

**② 模块树要有 `wheels`。** 动作族（move/turn/steer/shake/track）声明占用
`MODULE_WHEELS`，树里没有该节点 start 时直接 `KeyError`。树加一条支：

```
root → [code, car] → wheels
```

`mossv2/modules.py` 的 `MODULES` 与清单的 `modules:` 段是同形的两份，必须
逐字一致（这次顺手给 root 补了 `name: 根` 让两份真的一样，预检里带等值断言）。

**一条没解决的问题，明写**：模块只能表达**互斥**，不能表达**归类**。所以
`car/status`、`car/where`、`car/map`、`car/navigate*`、`car/stop` 不声明模块
（它们跟动作不冲突），在清单里落到「通用(不占模块)」那一段 —— 名字自带
`car/` 前缀，读的人不会误会，但分组名确实是"通用"。要让它们归到 `car` 组，
得在 `capability` 里加"没有模块就看名字前缀"的第二条分组规则；这次没加，
理由是分组只是排版，而多一条分组规则就是多一个要解释的机制。

**③ 词表 14 个，注册 60 个。** 注册 ≠ 词表：包整个进注册表（目录条目，
包 `__init__.__all__` 就是 manifest），但只有词表里的能写标签（`shell._route`
当场拒）。**不上词表的两个，都是"本进程没有它的生产者"**：

| routine | 等谁 | 为什么本进程没有 |
|---|---|---|
| `car/gaze` | `agent_status`（xml_agent） | mossv2 没有 xml_agent，它会被 passive 拉起后永远空等 |
| `car/wake_track` | `voice` 的 wake 帧 | 本进程的耳朵走 asr，从不发 wake |

`car/snap`（车头相机）原来也在这张表里：拍照要 `bus_listener`，而 device_bus 不在
这份清单。2026-10-25 把 `routines/user/device_bus` 装进清单后它算"有生产者"了，
于是上词表（查询类，`observe: True`，并把落盘的图随载荷 `images` 附上）。bus_listener
自己**不上**词表：它是器官，`car/snap` 才是它的面。

**④ 提示音：没有 `sound_effect` 就不响，也不刷 WARNING。** `MotionRoutine._beep`
原来直接 `call('sound_effect')`，失败就打 WARNING —— mossv2 这具身体没有说话
喇叭外设（它自己用 TTS 说），于是**每动一下刷两条 WARNING**，看着像车坏了。
改成先查一次目录（类级缓存 `_has_beep`）：没有这条 routine 就静默跳过，真调用
失败才告警。提示音是可选外设，不是运动的前置条件。

**⑤ 必须重启 moss，热重载不够。** watcher 热重载 yaml 时会 reload `Moss`
（kwargs 变了）→ 重搭 body（新词表进 shell），新增条目也会注册 —— 但
**模块树只在 `kernel.create` 时建**，`modules:` 段不在 watcher 的 diff 里。
所以热重载之后是"词表有了、树没有"的半截状态：`<car/move/>` 会报
`module 'wheels' not found in tree`。判据：**改 `modules:` 段 = 必须重启**。

**验证**（不起真车，不碰麦克风）：清单预检（条目存在 / `yaml_modules` 与
`MODULES` 等值 / 每个类和它声明的模块都在树里 / 每个词表名都能被扫描成
`ChildOpen` 且 `cmd` 原样）；`test_body` 13 例（新增 `t_namespaced_name`）；
`test_prompt` / `test_mutex` / `test_ctml` / `test_dispatch` 全绿。另外用
`test_prompt` 的装配环境渲染了一遍真实能力清单，确认 5 个动作落在 `car:` 组
并带 `(wheels)`，其余 6 个在通用段。

## car 全族的回执错位 + 错误那一行不该说内部话（2026-10-24，用户："这个回执也有问题 涉及内部实现了吧?"）

**现象**：车挂上来第一次问状态就炸 ——

```
obs> error(car/status): car/status: 回执只认 content / details / observe,
     多了 battery_pct, odom_theta, odom_x, odom_y, voltage_v
     (信封上的键由 channel 写, 不用门操心)
```

不是参数写错，是**契约错位**：回执契约（`content`/`details`/`observe`，见
`frames.py`）是后加的，而 `car/` 与 `hiwonder/` 这两族是老世代写的，13 个
`run` 一律 `return XxxOutput(...).model_dump()` —— 把 pydantic 结果平铺出去，
键全是业务字段。`receipt` 是**唯一校验处**，于是这 13 扇门在模型第一次伸手时
全部变成错误帧（`car/move` 那次也炸了，同一条）。

**① 全族改 `{'content': 人话一行, 'details': 结构}`。** 五个动作
（move/turn/steer/shake/track）在 `motion._execute_wave` 一处收口，其余各写
各的：`status` 加 `_summary()`（电量/位姿，缺什么直说，USB 假读数单独点出来），
`where` → `在「客厅」`，`map` → 路径 + 格数 + 定位不可用时"图上没画当前位置"，
`navigate` → `已发往「客厅」(x, y)`，`navigate/manage` → `导航栈已启动, 现在在跑`。

**② 错误帧那一行也是给模型看的。** 原来那句是写给**改 routine 的人**的：
契约字段名 + "信封上的键由 channel 写, 不用门操心"。模型动不了 routine 的返回
结构，把内部术语塞进它的输入只会教它复读，再顺手重试一次同样会失败。改成
`_broken` 一处收口、两个读者分工：精确原因（多了哪些键、哪个字段不可序列化）
`_log.error` 进日志；帧里那一句只说 `这个能力没交回结果(它自身出错, 重试也一样)`。
判据：**错误帧的读者是模型，不是写门的人** —— 要写内部话，写在日志里。

**③ 装配面要有一条静态线。** 运行时校验只在**被叫到**时炸，于是一整车能力能
带着错的回执上车。新增 `tests/test_receipts.py`：读 `app/moss.yaml`，AST 扫
清单里每个 `run` 的 return，判死三种形态（`None` 放过 / dict 键 ⊆
`content/details/observe` / `XxxOutput(...).model_dump()` 直接错），委托
（`return await self._execute(...)`）交运行时。带自检 `t_guard_bites`（守卫
自己要能咬人）+ 下限 `checked >= 20`（清单解析坏了不许假绿）。车身那批文件带
BOM，`ast.parse` 要用 `utf-8-sig`。

**④ `test_frames` 加 `t_gate_speaks_world`**：四种坏回执的错误行里不许出现
`content`/`details`/`observe`/`信封`/`channel`/`载荷`，但必须出现能力名。

## 错误帧的第二层内部话：pydantic 原文（2026-10-24，用户贴 `error(car/shake)` 那条）

**现象**（`<car/shake mode="y" freq="1.5" amp="0.8" cycles="4"/>`）：

```
error(car/shake): 1 validation error for CarShakeInput Value error, x/y 模式 amp 须
〈= 0.6 m/s [type=value_error, input_value={'mode': 'y', ...}, input_type=dict]
For further information visit https://errors.pydantic.dev/2.13/v/value_error
```

同一类病，另一种来源：错误帧的 `text` 原本是 `str(exc)`，而 **pydantic 的
`__str__` 不是给人读的**（类名 / 整份入参 dump / `type=...` / 文档链接）。
`_broken` 管住了我们自己抛的话，管不住库抛的。

**① 产地收成一个 `frames.failure(exc)`，与 `receipt` 成对。** 成 → `receipt`
（载荷三字段），败 → `failure`（一行 text），`channel._run_one` 的 except 只调
它，别处不再自己 `str(exc)`。判据：**同一件事只有一个产地**，第二个产地就是
下一个漏话的口子。

**② ValidationError 只留三段：哪个字段 / 允许什么 / 给了什么。**

```
error(car/shake): 参数不对 —— x/y 模式 amp 须 ≤ 0.6 m/s
error(car/shake): 参数不对 —— amp: 要 ≤ 2(给了 9)
error(car/shake): 参数不对 —— mode: 只能是 'yaw', 'x' or 'y'(给了 nope)
error(car/shake): 参数不对 —— cycles: 要整数(别带小数)(给了 3.7)
```

- 作者 `raise ValueError('…')` 那句话**原样留下**（本来就是人话），只剥 pydantic
  加的前缀 `Value error, `；
- 常见 type 各给一句中文（`missing` / `literal_error` / `less_than_equal` /
  `greater_than_equal` / `int_parsing` / `int_type` / `int_from_float` /
  `float_*` / `string_type`），模板要的键不在 `ctx` 里就退回原话；
- `input` 只在**字段级且是标量**时才印（`(给了 9)`）—— model 级错误的 `input`
  是整份入参，印出来又是 dump；
- 最多 4 处，多的折成 `…共 N 处不对`；
- 非校验异常（`RuntimeError('导航栈未启动…')`）**原样穿过**。

**③ 顺手修掉重复的能力名。** `_broken` 原来把 `verb` 写进异常文本，而
`frame_line` 又拼一次 `error(verb):` —— 帧上印两遍（用户那条日志里就是
`error(car/status): car/status: …`）。能力名只由 `frame_line` 拼。

**④ 根因的另一半：清单不印范围。** `_sig_line` 只印参数名与默认值
（`car/shake(mode='yaw', freq=2.0, amp=1.5, cycles=5)`），`amp` 的"随轴变"
只写在字段 description 里，**从不进 prompt** —— 模型拿 0.8 试出来不是没看，
是没处看。这一轮只把上限写进 `meta['description']`（`car/shake`、
`car/move`），**渲染器不动**："签名要不要印范围/枚举" 仍是未决项（见旧账
"签名不印类型/enum"），拍之前不许我替它发明一种印法。

**验证**：`test_frames` 新增 `t_failure_text`（四种形态 + 不许出现
`ValidationError`/`pydantic`/`input_value`/`http`/`Input should be` + 作者
异常原样穿过），`t_gate_speaks_world` 改成过 `frame_line` 并断言能力名只
出现一次；`test_dispatch` 全绿（错误帧断言不受影响：投递口的拒收文案是自己
写的）。

## 参数说明进清单：签名一行 + 参数子行（2026-10-25，用户："desc 理论上就该进入能力提示词吧?"）

**原状**：`_sig_line` 只用 `input_schema.properties` 的三个字段（key /
`required` / `default`），`p['description']` 取完默认值就再没被读过 —— 77 条
参数说明一条都没进 prompt。它们唯一还有人读的地方是 `shell.py:309` 的投递口
白名单和 `code_kernel` 的同款（都只取 key）。context 里那句"参数看清单里它
那一行"（`protocol.md` 的「签名即能力」）于是成了空话，`amp=0.8` 是**没处看**
而不是没看。

**原版对位物**：能力面是 `<interface>` 块 —— `async def bar(arg1: int, ...)`
加三引号 docstring（`core/helpers/func.py:80 to_interface`，CTML 提示词
`v1_0_0.zh.md` 第 81-84 行就是形态样例）。**docstring 就是参数说明的家**：
参数语义天生在提示词里，不是"额外补充".

**做法**：`_item` = 签名行 + `_param_lines` 子行 + example 块。
子行形如 `  amp: 速度峰值: yaw 模式为 rad/s(<=2), x/y 模式为 m/s(<=0.6)` ——
缩进两格（与 example 同深），只印**有**说明的参数（没写就不占行，空白行比
没有说明更坏），说明折成一行（作者可能写多行）。签名本身不变，仍然一行可
扫读。**子行与 example 之间空一行**：两者同缩进，不隔开时示例首行会被读成
上一个参数的续写（`bb_write` 的 "at 是基线…" 正是这种形状）。

**为什么不做行内括注**（`amp=1.5⟨速度峰值…⟩`）：最长的一条参数说明 113 字
（`car/wake_track.align_margin` 讲"转过头再转回头"的根因），内联会把一行
撑到 300+ 字，那行还要被扫读。子行只是多两行。

**代价**（按本装配实测 39 个词表项 / 77 个参数 / 77 条说明）：清单正文
9523 → 12279 字（1.29×，+2756 字 ≈ +1.7k token），77 条子行；7 项无参数
说明不占行。前缀在装配时渲染一次，不每拍重算（前缀缓存口径不变）。

**顺带撤掉一处临时补丁**：上一轮因为"清单不印范围"，把 `amp≤0.6` 硬塞进
`car/shake` / `car/move` 的 `meta['description']`。现在范围在参数自己的
description 里了，那两句退回本分（只说"是什么"），不两处说同一件事。

**未做**：`protocol.md` 没有一句解释"签名下面挂着的那几行是什么"（形态上
`参数名: 说明` 大概率自解释）。若要当机制通告，得在那儿加一句 —— 那是用户
手改的文件，不代改，只报告。

## 能力清单两级披露：detail 点名详列，其余只印名字（2026-10-25，用户："不应该一次渲染所有 routine 进入提示词，除了核心 routine 以外，都只需要以名字形式进入即可"）

**缘起**：上一轮把参数说明塞进清单，正文 12573 字，全量详列的做法本身没被
质疑过 —— 用户这一句质疑的是"全量"。前缀是**每一拍都在**的常驻成本，而
真正每拍都用得上的只有语法与轨（say / `_` / all / code / interrupt）和黑板
写入口；车身能力一次对话摸不到两次。

**判据落在装配（清单的 `detail:`），不在代码里**：与"装配参数写清单而不是
代码默认值"同一条原则。名单内的详列（签名 + 参数子行 + 示例），名单外的
在所属分组末尾收成一行 `  另有: a b c`。换 profile 就换披露面，代码不用动。

**为什么仍要给裸名留一条查法**：名字在眼前、参数不在，等于把"不知道传什么"
换成了"猜"。错误回灌能纠正（`failure()` 会说"要 ≤0.6"），但语音场景每次试错
都是一拍 —— 所以裸名的用法挂在一个按需查询后面：

```
<routine/doc name="car/move"/>   →   清单里那一项(签名/参数说明/示例)
```

**这不是新造的能力**：`app/routines/user/routine/doc.py` 早就存在（老清单
`routines.yaml` 装配它，skill 库拿它当软依赖），只是 `moss.yaml` 没注册它
—— 在 mossv2 这具身体里模型看不见。**先装上已有的，不要另起一个名字**：
中途我曾写过一个 `commands/help.py`，两个入口两种渲染，那才是真正的重复。

**改造它而不是包一层**：① 返回 `{'content': …}`（旧形状 `{'doc': …}` 会被
回执契约当场拒 —— 和 car/status 那个 bug 同一类）；② 渲染走
`prompt/capability.entry()`，与清单那一项同一个函数，不存在第二份文档要
同步；③ meta 加 `observe=True`（取用法就是为了看结果）；④ 只查 catalog
（kernel 路由表），不经过 shell —— 这份 routine 被两具身体共用，不该为
"查文档"依赖某具身体里有谁在跑。消费者同步：`dynamic/evolve.py` 读
`content`（它靠 doc 里的描述判断热重载是否生效）。

**裸名就是名字**（~~保留 `⟨body⟩` / `⟨observe⟩` 两个机制标记~~，2026-10-25
撤掉 —— 见文末"清单里不印标记"）。

**`detail` 缺省 = 全详列 + 一条 warning**：没配就丢信息比多花 token 更坏。
`detail` 里写错名字 = 那一项退回裸名 + 装配期 `_log.error` 点名，但**不拖垮
装配**（拉起失败 = 灵魂不在，一个笔误不该让整具身体起不来）。标签面写法
`_` 也认（`canon()` 一处映射到 `__scope__`）。

**实测**（41 词表项，本装配的默认名单 16 项详列 / 25 项裸名）：正文
12573 → 6546 字（52%，约 −3.8k token）。裸名 25 项的名字合计约 420 字 ——
这就是"知道有这个能力"的常驻底价。

**黑板不算核心**（用户定的）：`bb_write` / `bb_objects` 从 `detail` 撤出，
整族 14 门一致按名字进清单。判据是"详列 = 说话与编排必须一次说对的东西"
（语法、轨、`say`、`code`、`notify`、`routine/doc`）——黑板是画的时候才需要的
工具，画之前查一次是正常成本，不是每拍都要复现的知识。

**`noop` / `asr_mode` 留在详列**（用户定的）：两者都是"每拍都可能用到、且
参数本身就是语义"的东西 —— `noop` 的整个含义就是"写这一个词", 只印名字
等于逼模型自己猜这个词为什么存在（它会被读成无意义的填充）; `asr_mode`
的 `mode` / `button` 是它唯一的表达方式。`asr_mode` 另加**出厂 `observe`**:
它的两种用法（不给参数查模式 / 切完确认）都以回执为结果 —— "现在在哪个
模式"是它下一步说话的前提, 不该靠调用处每次记得写 `observe="true"`。

**测试**：`test_prompt` 新增 `t_detail_split`（点名详列、其余收一行、标记跟
名字走）、`t_detail_unknown`（写错名字忽略但不炸）、`t_doc_returns_entry`
（doc 回的就是清单那一项；用裸名项的 example 验证它确实从 doc 来的——
`FakeBrain` 新增 `seen_inputs` 记录每拍 history）；`t_registry_read` 跟上
`detail` 键。`test_receipts` 自动把 `doc.py` 纳入（66 模块 / 56 run，返回形状已合规）。

**未做**：`protocol.md` 里没有一句讲 `<routine/doc>`（"清单里只列名字的那些怎么
查"），也没讲"签名下的子行"；那是用户手改的文件，只报告不代改。




## 测试按改动面选，不每次全量（2026-10-25）

用户：「不要每次都全量dispatch 根本没有必要」。全量 `test_dispatch` 串行
7m36s（并行入口 `tests/run_parallel.py`，8 片），跑完一轮只换来"这次改的渲染
没破投递" —— 那个结论局部测试早就给了。**判据：这次改动是否可能改变"帧怎么
被解释"**。改文本渲染、改某个 routine 的返回、改清单条目，都不会。

| 改了什么 | 最小验证 |
|---|---|
| 能力面渲染（`prompt/capability.py`）、`detail` 名单 | `test_prompt` + 清单预检 |
| 某个 routine 的 `run` 返回 / `meta` | `test_receipts`（AST 扫清单里每个 run）+ 该族的测试 |
| 回执契约 / 错误帧口径（`frames.py`） | `test_frames` |
| `body.py` 词法（标签名 / body 还原） | `test_body` |
| 黑板（`board/` + `commands/board`） | `test_board` |
| 耳朵（`asr/` + `listen/`） | `test_listen` |
| `notify` / 礼仪 | `test_notify` |
| `brain.py`（输入渲染） | `test_brain` |
| `ghost.recall` 只加只读字段（不碰拍链/门禁） | `test_brain` + `test_ui` |
| `key_listen` | `test_keys` |
| UI（`ui.py`） | `test_ui` / `test_ui_think` |
| **`shell` / `channel` / `ghost` / `mindflow` / `container` / CTML 调度** | `test_dispatch`（或 `run_parallel -j 8`） |
| **`modules:` 段 / 条目增删 / 词表名字与注册是否成对** | 清单预检（`check_yaml` + 模块树与 `MODULES` 等值）+ 一次装配冒烟 |

`test_receipts` 与清单预检都是秒级，随时可跑；`test_prompt` 约十来秒。**只有
动"帧的解释"才值得等 dispatch** —— 其余时候它就是 7 分钟换一句"应该没事"。


## UI 得能看"它读到什么"（2026-10-25）

用户：「ui需要支持查看系统提示词 和history」。

这两样东西一直在系统里，但从没露过面：`recall` 早就返回 `system`，UI 只取了
帧链那一半。要判"它为什么这么答"，得能看它**被告知什么**(系统前缀)和**读到
什么**(历史)。

**一份 recall 供三页**：帧链(这一拍干了什么) / 历史(这一拍读到什么) / 系统
提示词(始终被告知什么)。另开两个拉取口 = 两套时序，翻页时三页对不上同一
时刻；一次 req 供三处，天然同源。

**历史页给"模型读到的原样"，不给记忆条目的转述**：`recall.history` 是回合制
条目(结构化)，`model_input` 才是脑子把它渲染成的块(`<percepts>`/`<listen>`/
`<observe>`/`<dynamic_context>`/`<stop_reason>`/`<recap>`)。用户问"模型看到
什么"，答案就是那串块。

**渲染只有一处产地**：`Brain._input_items` 提成模块函数 `input_items(history)`
——`stream` 用它，`Brain.render_history = staticmethod(input_items)` 供回读。
ghost 侧是**可选能力探测**(`getattr(self._brain, 'render_history', None)`，脑不
提供就是空表，与 `on_reasoning` 同构)，所以灵魂不认具体脑子、UI 不认脑子。
UI 自己照着块规则拼一遍 = 第二套真相：拼歪的那份看起来同样正常，而且永远不会
报错。

**系统提示词给原文，不重算**：`recall.system` 是装配时 `'\n\n'.join(frags)`
拼好那一次的结果，也是每拍随请求发出去的 instructions。重新调一次 `prompt/*`
片段会得到"现在的清单"，不是"它正在用的那份"。

**长文页懒排 + 同内容不重建**：历史几十条、前缀一万字，快照 250ms 一份。
`TextPanel.set_text` 字面相同直接返回；页签不可见不排(切过去才排)。否则每来
一条回执，正读到中间的人就被弹回顶部。

**用 `QPlainTextEdit` 不用 `QTextBrowser`**：两页都是原文——等宽字体读得出缩进
与标签层级，原生给选择/复制/滚动；富文本化(给 role 上色)会让人以为原文里本来
有那些颜色。另给「复制」「弹出」(侧栏太窄，长文要整窗读)。

**载荷键名只写一处**：`context_payload(recall)`。recall 少字段是**静默**的
(页面就是空着)，键名在 UI 与测试里各写一遍 = 改一边忘一边，症状只是"页签
空白"，没有任何报错。

**测试**：`test_ui` 新增 `t_three_tabs`(一份快照供三页 + 长文页懒排)、
`t_empty_context`(空快照给占位不炸)、`t_text_no_rebuild`(同内容用
`document().revision()` 断言没重建)、`t_history_labels`(模型侧 role 全是 user，
按首行 tag 才分得清感知/观测/瞬态)；`test_brain` 加两条同源断言：
`Brain.render_history is input_items`、`recall.model_input[0] ==
client.calls[0]['input'][0]` 且 `recall.system == instructions`。

**实测**(临时最小装配跑在 `127.0.0.1:7888`，避开在跑的实例)：系统提示词
10815 字；一条反射弧后 历史 0 → 1 条(它的话) → 2 条(加 `<observe>`)，帧 0 → 1。

**踩到的坑**：改 `ghost.py` 触发 watcher 换实例——`Ghost` 要 `shell_id`/
`system`，只有 Moss 能给，于是**在跑的那套灵魂掉了**(`ghost` 不在
`get_running_routines` 里，shell/mindflow 还在)。判据照旧：改装配件后
**重启 moss**；`ui.py` 改了还要**重启 UI 进程**，否则窗口里还是旧代码的侧栏。


## 清单里不印标记, body 的字段也不再由作者写（2026-10-25）

用户看到 `bb_arrow ⟨observe⟩` 这一行：「不需要括号说明 obs 或者 body，name 即可；
body 内容应该放 desc 就行了，不需要专门的 meta 字段」。

拆成两件事，落点完全不同 —— 一件在**渲染**，一件在**声明**。

**一、渲染：清单只印名字与说明，不印 `⟨…⟩`。** `signature()` 不再追加
`⟨body: …⟩` / `⟨observe⟩`，`name_only()` 回到纯名字。原来那两个括注想通告的
机制，两头都不在清单上：body 怎么写是它自己 `description` 里那句
（`say — 说话: body = 要说的话…`，全部 6 个带 body 的 routine 本来就写着），
而"这一次要不要结果"由标签上的 `observe="true"` 现决定（`shell._route` 三侧
同名 or 合并）。同一条事实说两遍的代价是**每拍都付**，收益是零；而 25 个裸名
每个背一个括注，正好把真正每拍要用的语法淹掉。`另有:` 那行是**索引**，它只需
回答"这个词存在吗"。

**二、声明：`meta['body']` 默认是"不收"，收 body 的类自己写 `'body': True`。**
（这一条当天又被推翻过一次，见下一节 —— 先留原文，因为它把"为什么不是盖旗"
的另一半讲清楚了。）

~~"收不收 body"是**类族性质**（在这个基类下面 = 收），不是作者该手写一遍的
字段~~ —— 当时的理由是"手写的那份会和这个类到底是不是 `CtmlRoutine` 漂移"。
这个理由只对了一半：漂移的代价取决于**漏写时机制说什么**。如果漏写变成静默，
手写确实危险；但现在有闸门（`shell._apply_meta`），漏写的下场是**当场一句
"内容只能写进参数"**，不会静默。所以真正该问的是：漏写哪一侧更贵。

旗子的两个读者都在机制侧：`shell._apply_meta`（喂 body 给不收的 routine 当场
拒）与 `code_kernel._scan_no_body`（代码面 `call(name, body=…)` 同一道闸）。

顺带把 `bb_write` 的「`at` 是基线」这类话留在 example 里 —— 它们本来就只能
在这（参数子行装一句就够，塞两段会被读成续写）。

**`observe` 的机制没动**：`meta['observe']` 照旧进 `_observe_verbs`（出厂声明
= 每次都要结果）。它只是不再**印**——那是提示词的事，不是机制的事。

**测试**：`t_no_markers`（真实渲染里一个 `⟨` 都不许有，并逐行比对
`- obs — 查询类: 结果自动回到眼前`）、`t_body_declared`（9 个收 body 的类都
显式写了 `'body': True`；继承 `CtmlRoutine` 但不写的类没有这个键）；
`t_detail_split` 的"另有行带标记"断言翻成"不带"。

**`protocol.md` 有三处指向旧标记**（第 47 / 105 / 193 行）—— 那是用户手改的
文件，不代改，已单独报告。


## `meta['body']` 默认是不收，收的要自己写（2026-10-25 定稿）

上一节先写成"由 `CtmlRoutine` 盖旗（默认收）"，接着又试了 `setdefault`（默认收、
可写 False）。用户一句话把它翻过来：「默认是 false 吧，不支持 body」。

**这条是对的，而且理由不是"少数服从多数"，是"漏写时哪一侧响"。** 我一开始把
账算反了：

| 默认 | 想收的作者漏写 | 下场 |
|---|---|---|
| 收（盖旗） | 不适用（不写就是收） | —— |
| 收（盖旗） | **不想收**的作者漏写 False | 字节投进去 → 没人读 → **静默消失**，回执还正常 |
| 不收 | **想收**的作者漏写 True | 字节不投 → shell 当场一句"内容只能写进参数" → **响的失败** |

"漏写"的代价取决于机制说什么。默认收时，漏写 False 的那一侧是**静默**——最坏
的失败形态，而且回执正常会让模型更相信内容生效了。默认不收时，漏写 True 的
那一侧有一句错误帧、还自带注意债（`_record_trace`），下一拍就矫正。所以默认
落在"不收"。

第二条理由更根本：**meta 是机制的输入**。机制读到的每个键都该在 meta 里看得见；
靠 `__init_subclass__` 推导出来的 `True`，读 catalog、看日志、调试的人说不清它
是从哪来的。声明面就该写全。

落法：删掉 `__init_subclass__`，9 个真正收 body 的类各自在自己的 meta 里写
`'body': True`（`Say` / `Code` / `Container`（`All`/`AnyOf`/`Flow`/`Loop` 用
`**Container.meta` 展开，一份声明管全族）/ `BbWrite` / `BbPixel`）。

不担心漏写，因为漏写是响的：`<say>你好</say>` 若忘了声明，立刻报"say 不收
body"，说话直接坏掉 —— 这种失败不可能被忽略。

**剩下的那半仍然不做**：routine 收到了 body 但读不了、想事后报。时序上 run 的
返回早于 body 到达（`channel._run_one`：`start()` → `await handle` → `receipt`），
"在 run 里附带结果返回"只对已经在等 body 的那批可行 —— 而那批正是收 body 的。
详见 `2026-10-25` 工作记录的时序论证。

**测试**：`t_body_declared`（9 个类都有显式 `body: True`；继承 `CtmlRoutine` 但
不写的临时类没有这个键；`X`/`Obs`/`Sig` 没有），`test_ctml` 的 `_Probe` 补了声明。

## 听来的话要把"听得怎么样"一起交给模型（2026-10-25）

用户问：「我记得原版通过 asr 拿到的结果跟文本是不一样的，模型能够知道？」

能知道"这是听来的"（`source != 'user'` 包 `<listen>`），但**不知道"哪几个字可能
听错"**。原版在 `<listen>` 上还有三个属性（`listener_nucleus._listen_message`）：

| 属性 | 原版语义 | 我们（改之前） |
|---|---|---|
| `low_conf="说的"` | 词级置信度低于礼仪阈值的那些字，**只列字不列分数** | 没有 |
| `db="54.2"` | 这一段的平均响度，"是测量，不是对任何人的判断" | 算了，只在 `asr/status` 里露过面 |
| `paused="false"` | 这包是段的中间还是收尾（信息，不是许可） | 有等价物（`complete`），没印 |

原料本来就在手里：`listen/events.Word.conf` 是逐字置信度（火山
`utterances[].words[].conf`），`asr/ear._rms_db` 是每块 dBFS，阈值
`deliver.low_confidence` 早就配好了。断在两处：耳朵只传 `text`（words 在
`asr/ear` 内部就丢了），`Impulse` 没有放这三个数的位置。

**为什么值得补**：模型会把听错当真。用户说"帮我关一下灯"，识别成"帮我灌一下
灯"，模型看到的是一句干净的话、没有任何可疑标记 —— 于是按字面理解，然后困惑。
`low_conf` 就是干这个的：只把可疑的那几个字标出来，让它知道"这里可能是同音字"。

**落法（五处，一条链）**：

1. `asr/ear`：`EarEvent.words` 带词级明细 `[{'text','conf'}]`（`conf`/`confidence`
   两种键名都认，缺失 → `None`）；`level_db()` 探针给"最近一块像人声的 dBFS"
   （静音块不更新 —— 判停之后 `_last_db` 已经掉到 -60，拿它当读数会把每一段
   都报成静音）。
2. `listen/controller`：`level` 探针**在收到文本事件时**采一次样（那一刻人一定
   在说话），段内求均值，`_commit` 里**先结账再切段**（切段会清 `_clauses`，
   反过来读就是"这一段没有 db"，静默）；`_low_confidence_chars` 只拼字。
3. `Impulse`：`low_conf: str` / `db: float | None` / `paused: bool | None`，
   `declare` 直读。
4. `mindflow._percept_attrs`：属性**按源挂**（`{源: {属性}}`）—— percepts 本来
   就按源 keyed，属性只能跟着同一个 key 走；同源多包以尾包为准。随 `_think` 的
   `percept_attrs` 同行下发（与正文分两条路走迟早对不上）。
5. `Moment.percept_attrs` → `render()` 的 `attrs` → `brain._listen` 拼属性。

**三个渲染上的取舍**：

- **`paused` 只在 `false` 时印**。现在进脑子的包都是收尾包（起话包被同 id 尾包
  吸收），恒真的属性写进每一行只是每拍付一遍字数。机制留着：哪天中间态也进
  脑子，模型得能分辨"他还在说，别抢答"。
- **空值不占位**。`low_conf=""` 只会让模型去猜空串什么意思（原版
  `gen_attributes` 同规矩）。`db` 为 `None` 同理 —— "没量到"和"很安静"是两件事。
- **属性值转义**。识别结果理论上可以含 `<`；结构被撑破不是"显示难看"，是模型
  看到坏 XML 去猜边界，猜错整句读歪。

**`db` 用段均值而不是"最后一块"**：原版逐帧累加（`_on_audio_level` 跳过静音帧），
我们改为"有字才采样"，口径一样（段内均值、段尾结算），差别只是采样密度 ——
听侧本来就不该逐帧参与，那是器官的事。

**测试**：`test_listen.t_hearing_facts`（只列低分字、`conf=None` 不算、db 段均值、
第二段不沿用上一段、阈值 None 不列字、探针没挂就没有 db）；`test_brain.
t_listen_attrs`（三个属性各印各不印、转义）与 `t_listen_attrs_flow`（端到端：
`declare` 带读数 → 脑子吃的输入里真有属性 —— 中间四层抄错键名的话，模型看到的
是一句干净的假话，而全链路不报错）。

> **渲染那半后来翻了**：`low_conf` / `db` 暂不印，只留 `paused`。见下一条。

## `low_conf` / `db` 暂不进 prompt，只留 `paused`（2026-10-25）

上一条把三样都接到了 `<listen>` 上，落完之后用户看了渲染样例，说：「low_conf="灌"
db="54.2" 这俩暂时不需要」。

**只关闸门，不拆管道**。"暂时"是关键：两个读数照旧从耳朵采、经 `Impulse` 送到
`Moment.percept_attrs`，唯一改的是 `brain._listen` 不再拼成属性 —— 哪天要露给模型，
加两个分支就够，上游一层都不用动。判据是**闸门只有一个产地**：模型看见什么只由
`_listen` 决定，所以关在这一处既完整又可逆（相反，若在 `asr._emit` 就不发这两个字段，
数据就真没了，`asr/status` 与日志里的排障读数也一起丢）。

**代价要认**：数据在流、没有提示词消费者 —— 很容易被后来的人当成"链路断了"。
所以三处注释都写明"attrs 里有这两个键是正常的"，测试也改成正向断言"不许漏到标签上"：

- 旧 `t_listen_attrs` 断言 `low_conf="汽"` / `db="54.2"` **印出来**；新的反过来：
  attrs 带着也不许出现在 `<listen` 行里，并断言那行只剩 `source`/`created` 两对引号
  （数量断言挡的是"以后加了新属性又忘了更新测试"）。
- `t_listen_attrs_flow` 改传 `paused=False`：既证明读数穿到了渲染层（`paused="false"`
  出现），又证明另外两个没漏出去。
- 转义用的样例从 `low_conf` 换成 `source`（`_attr` 仍在用，别连它一起测没了）。
- `test_listen.t_hearing_facts` 只断数据面载荷，不受影响，只改了 docstring。

**顺带的好处**：`prompt/protocol.md` 里本来就没解释这两个属性（只写了
`source` 的来源），撤掉渲染之后，提示词与渲染面反而一致了 —— 不再是"给了两个
没解释过的属性名"。

## 交互面主区改成 LLM 边界流水，不是对话框（2026-10-25）

用户：「ui也改成真实的输入输出吧」（在讨论完原版为什么把输入也包成 XML 之后），
问到范围时选了「主区整片换成真实流水」。

**改之前**：主区是混合体 —— 它那一侧已经是 logos 原文（`_on_text` 直接插
CTML），我这一侧却是 `你: 你好`，观测是 `obs> say: ok` 一行摘要。同一个界面里
一半是真的、一半是我转述的，而转述的那半**看起来同样正常**。

**改之后**：主区只剩两样真的过过 LLM 边界的东西 ——

- 进来的那半 = `model_input`（脑子自己渲染的 `input_items` 产物，与喂进模型的
  是同一份），整段照抄；
- 出去的那半 = `moss/text` 字流（logos 原文），逐 chunk 追加。

没有 `你:`/`Moss:` 前缀，没有块头标题（历史页才有，那是用来翻查的索引），块
之间只有一条等宽横线 —— 谁说的**由内容自证**：`<percepts>` 起头就是进来的，
`<say>` 起头就是它写的。这跟我给"为什么输入也用 tag"的答复是同一个判据：既然
原文自证，加任何标签都是第二套说法。

**三样东西被移出主区**（判据：它们没有真的进过模型）：

| 东西 | 去处 | 为什么 |
|---|---|---|
| 我正说着的话（`heard` partial / 刚回车的字） | 输入框上方灰字行，标"未入史" | 它下一拍才随 `<percepts>` 入史；先画进流水，屏幕上就有句话模型没收到的 |
| 回执 / 打断 | 状态栏一行摘要 | 下一拍会以 `<observe>` / `<stop_reason>` 块出现，那个才是模型读到的；主区再印一遍 `obs>` 就是同一事实两套说法 |
| 思考流 | 主区里的灰字旁注（保留） | 严格说它既不是输入也不是输出 —— 但首字要等八九秒，它是唯一还活着的信号 |

**"转正"是自动的**：`_apply_items` 拿每 250ms 的快照跟上次比，`model_input`
一变就清掉草稿并重排 —— 草稿消失那一刻，就是它真的进了模型的那一刻。这一条
把"界面上有"和"模型收到了"钉成同一件事，是整次改动的意义所在。

**实现取舍**：

- **`QTextBrowser` → `QPlainTextEdit`**。原文不需要富文本，而且 `setPlainText`
  比 `setHtml` 快一个量级 —— 每拍整段重排（窗口上百块）才不心疼。等宽字体照旧
  （标签缩进/对齐是原文的一部分）。理由同历史页：加了颜色和标题，人就会以为
  原文里本来有那些东西。
- **重排 vs 追加**：结构化那半（快照）整段重排，流式那半（logos）增量 `insertText`。
  全量重排每个 token 做一次会卡，而增量对快照无能为力（块会长出来、会变）。
- **`_live` 的生命周期 = 一拍**：`_on_obs` 清空它（拍末 logos 已 `commit_logos`
  进记忆，下一个快照会以块出现 —— 所以**不会重影**，`t_stream_appends_raw` 断
  言 `count == 1`）。`_rebuild` 时若 `_live` 非空会补在末尾，防重排打断流。
- **跟随滚动取"插入前"的判据**：插入会让 `maximum()` 变大，事后再比永远是
  "没在底部"。往上翻读历史的人不该被一条思考流拽回尾端。
- **同一份快照不重排**：`items == self._items` 就返回。250ms 一份，不比较就会
  每 250ms 把人弹回底部（历史页早有同一条，`t_text_no_rebuild`）。

**测试**：`t_transcript_text`（空条目跳过、块间一条线、正文空行缩进原封不动）、
`t_transcript_raw`（原文逐字在里面、无 `你:`/`Moss:`/块头、三块两条线、同快照
不重排）、`t_draft_is_not_transcript`（heard 只动灰字行 → 入史后草稿消失原文
长出）、`t_typed_goes_draft_too`（打字同一条路）、`t_stream_appends_raw`（三
chunk 拼成原文、无前缀、拍末不重影）。删掉 `t_heard_stream` / `t_heard_barge_in`
/ `t_typed_and_heard_share_style` —— 它们断的"你: "行已经不存在了。
`test_ui_think` 的 `t_text_closes_think` 去掉 `Moss: ` 断言。

**只改 `ui.py`，只重启 UI 进程**（主进程不用动）。

## 主区把输入的壳解成人话；快照跟拍改成心跳（2026-10-25，同日更正）

上一节说"主区照抄原文"，半天后就被推翻了。用户看到的是
`<percepts><listen source="asr">你好</listen></percepts>`，问「怎么能看不到输入那种
xml 格式?」——追问一句「我说的话怎么没有」，问题就清楚了：**壳一上，人读不出哪句是
自己说的**。

**根子在哪**：`<percepts>` / `<listen>` 是 `brain.input_items` 给模型看的排版，不是
人读的格式。它服务三件事（多源并列、属性增删、输入输出同构），三件都是模型的事，
一件都不是人的事。上一轮把它整片搬上屏幕，等于把给机器看的东西给人看。

**改法（只动 `ui.py` 的显示层）**：

- `percepts_text(body)`：只解开 `<percepts>` 一种块 —— 裸行 → `你: …`，`<listen>`
  → `你(听): …`。多行正文不合并（该断哪儿断哪儿）。
- 别的块（`<observe>` / `<stop_reason>` / `<dynamic_context>`）**原样留着**：它们
  不是对话，写成"它做了什么"才是转述。原文一个字没丢，右侧「历史」页签照旧是
  `model_input` 原文，那里不过这层解包。
- 实测 assistant 的 logos 本来就是纯文本（`t_stream_appends_raw` 喂的 `<say>` 只是
  测试夹具），所以输出那半不用动。

**同一次修掉的第二个 bug（更要紧）**：主区停在第一拍，后来说的话一条都不出现。

- 症状：屏幕上只有第一句 + 越堆越长的流，我后面说的每句都没进流水；而 `recall`
  里 6 条数据齐全（现网实测：`history 条数 = 6`，主区只有 1 条）。
- 原因：快照拉取只靠**事件点灯**（obs / 打断 / 发送 / 连接）。语音不经过输入框
  （`send` 不点灯），`say` 又没有回执帧（`ghost.on_message` 只在 `if obs:` 才广播），
  于是两个点灯口都不亮 —— 定时器是 `setSingleShot(True)`，等的是一个永远不会来的
  信号。
- 改法：`_ctx_timer` 改成**常开心跳**（1000ms，`setSingleShot(False)`），点灯退成
  另一盏 `_ctx_kick`（250ms 单发防抖）。心跳每秒一份，`_on_context` 先算整份指纹
  （`model_input`/`frames`/`pending`/`transients`/`recap`/`system` 的 repr），
  一样就整份不分发 —— 帧链面板是重建式的，每秒重放会把人正在读的位置弹走。
- 判据：**"界面上有"与"模型收到了"必须是同一件事，而它能自己跟上，不靠谁记得点灯。**

**现网实测**（离屏 UI 直连在跑的实例，`hub_id`/实例名加 `-probe` 避免挤掉用户的窗口）：

```
你: hi
──────────────────────────
嗨，我在。想聊什么，或者要我做什么？
──────────────────────────
你(听): 聊聊天
──────────────────────────
行啊，聊呗。…
──────────────────────────
你(听): 能干啥知道吗
──────────────────────────
知道。我能动，不只是能说。…
```

**测试**：`t_percepts_text`（裸行/听来/两样同拍/非输入块原样）、`t_transcript_text`
（输入块解成人话）、`t_transcript_raw`（壳不进主区、输出与观测块原样、三块两条线、
同快照不重排）、`t_draft_is_not_transcript`（入史后是 `你(听): …`）、
`t_typed_goes_draft_too`（`你: …`）、新增 `t_heartbeat`（心跳必须常开且非单发，
点灯仍防抖）。`test_ui` 11 个、`test_ui_think` 3 个全过。

**只改 `ui.py`，只重启 UI 进程**。

## 撤回解壳：主区要原版，不要渲染（2026-10-25，同日第三次）

用户：「要原版 不要给我渲染」。上一节那层 `percepts_text` 解包撤销。

**为什么撤**：解壳看着只是"换成谁说的"，实际是**第二套渲染**。它的输入是
`model_input` 的字符串，靠认 `<percepts>` / `<listen` / `</listen>` 这几个字面——
这些字面由 `brain._listen` 产出，只要那边改一个属性名、加一种块，这里就解析失败，
而失败的表现是"那句话没显示"，不报错。原文照抄没有这个面：模型读到什么，屏幕
上就是什么。

**关键区分**（这才是上一轮真正的教训）：`model_input` 已经是**渲染产物**了 ——
脑子把结构化记忆渲染成块，那是"唯一渲染产地"（`brain.input_items`）。主区拿它
照抄，不算重拼；主区再解一层，才是在产地之外又造一个解释器。

**"我说的话怎么没有"另有其因，已单独修**：不是壳挡住了话，是**快照停在第一拍**
（语音不点灯 + `say` 无回执帧 → 单发定时器等不到信号）。心跳那半保留，它和渲染
无关。修好后同一个窗口里 3 条输入都在，只是包在壳里。

**现状**：
- `transcript_text` 回到原样输出 `content`，一个字符不改；
- 新增/保留的只有心跳（`_ctx_timer` 常开 1s）+ 点灯（`_ctx_kick` 250ms 防抖）
  + 整份指纹去重；
- 删 `percepts_text` 与 `t_percepts_text`；`t_transcript_text` / `t_transcript_raw` /
  `t_draft_is_not_transcript` / `t_typed_goes_draft_too` 全部改回断原文；
  `t_heartbeat` 留着（它断的是那个真 bug）。

**现网实测**（离屏 UI 直连在跑的实例）：

```
<percepts>
hi
</percepts>
──────────────────────────
Hi. 有什么想让我做的?
──────────────────────────
<percepts>
666
</percepts>
──────────────────────────
哈哈,这声 666 我收下了。有活儿随时说。
```

**只改 `ui.py`，只重启 UI 进程**。

## 重影：`_live` 的结账判据改成"快照里有没有它"（2026-10-25，同日第四次）

用户截图：主区最底下**同一句回复两个块**，中间还隔着一条分隔线，而
`recall.model_input` 里只有一条。实测确认（只读探针连在跑的实例）：

```
[5] user      <percepts>\n666\n</percepts>
[6] assistant 再 666 下去,我要怀疑你是不是在测我还在不在了。在的。   ← 只有一条
```

**根因**：`_live`（流式累积的 logos 增量）原来只在 `_on_obs` / `_on_interrupt`
里清空，隐含假设是"拍末有回执帧"。但 **`say` 没有回执帧** —— `ghost.on_message`
只在 `obs` 非空时广播。于是纯说话的拍 `_live` 永远不清，下一拍重排时
`transcript_text(快照)` 画一遍、`_live` 再贴一遍 → 同一句两个块。

**判据改成从快照来**：`_settle_live()` 取走 `_live` 之后，看 `model_input` 里
**最后一条 assistant** 的 content 是否已包含它 —— 包含就不贴（快照已经画了）。
语义顺带收紧为"**上次重排之后**新增的字母"：出口只有这一个，重排即结账，
不可能跨拍累积。`_on_obs` / `_on_interrupt` 里的清空全删（回执不是可靠的拍末；
被打断的半句确实发出去了，也该留在屏幕上）。

**为什么不能用"重排就清"**：拍内重排（快照变了但这一拍还没 commit）时，
`_live` 是**唯一**还能显示那段话的东西，清掉就把正在说的话抹了。
`t_midstream_rebuild_keeps_live` 断的就是这一面。两条用例是一枚硬币的两面，
判据必须是"快照有没有它"，不能是"有没有重排"。

**顺带**：`say` 无回执这件事，上一节的心跳修的是"快照跟不上"，这一节修的是
"流式那半结不了账"。同一个假设（拍末有信号）两个不同后果，都是错的。

**测试**：`t_stream_appends_raw` 去掉 `_on_obs([])`（真实路径下它根本不会来）；
新增 `t_say_without_receipt_no_double`（连着两拍纯说话、`model_input` 累积 →
每句只出现一次、四条块三条线）与 `t_midstream_rebuild_keeps_live`（拍内重排
不许丢正在流的话、重排后流接得上不另起块）。已用 monkeypatch 把
`_settle_live` 打回旧行为验证过：新用例确实抓得住重影。

**现网实测**（离屏 UI + 只读探针连在跑的实例）：连上后 6 条全在；把最后一句
再"流"一遍（不喂回执）→ 屏幕上临时多一块（那是还没进快照的新内容，正确）；
快照到达 → 重排后出现次数 = 1。

**只改 `ui.py`，只重启 UI 进程**。

## car 系列漏了出厂 `observe`：查询类补上，动作类不补（2026-10-25）

用户：「car系列routine 没加obs吗?」。查了一遍，**13 个 car routine 一个都没写
`meta['observe']`**，而 `query_weather` / `routine/doc` / `asr_mode` / 黑板族 /
chan 族 / code 全都写了 —— 只有 car 这一族漏了。

**漏了的代价是静默的**：查询类（`car/status`、`car/where`、`car/map`）调它就是
为了那一句读数，不声明 `observe` 时结果只记账、不点灯，要等下一次有输入才进
`<observe>` —— 模型"查了一下，什么也没看见"，而这一拍已经过去了，它还会按自己
的想象答。`car/map` 最典型：`content` 里的 PNG 路径**就是全部产出**
（`details` 没有通往 prompt 的路），不 observe 等于让它白画一张图。

更麻烦的是：清单**已经不印** ⟨observe⟩ 标记（2026-10-25 按用户要求撤掉），模型
连"这是个查询类"的线索都没了，只能靠每次手写 `observe="true"` —— 而它没有理由
每次都写。所以这类能力必须在 `meta` 里出厂声明。

**判据（两条，互为反面）**：

| 类型 | 声明 | 为什么 |
|---|---|---|
| 查询类（只读，结果就是答案本身） | `'observe': True` | 调它等于提问，看不见回答就是白调 |
| 动作类（发出去就完了） | **不声明** | 声明了每动一下都多开一拍去等"跑完，已停"，白付一拍延迟；结果下一拍自然可见 |

补了四个：`car/status`、`car/where`、`car/map`（查询）+ `car/stop`（安全，零速
真的发出去了才继续 —— "以为停了其实没停"是最坏的臆造）。**不补**：`car/move`、
`turn`、`steer`、`shake`、`track`、`navigate`、`navigate/manage`（发出去就算做完；
失败会抛异常 → 错误帧自带注意债 → 该点灯的自己会点）。

**`'readonly': True` 是死键**：mossv2 里没有任何读者（只有 `agents/flow` 那套外部
包读它，用于 plan mode 白名单）。别拿它当"查询类"的机制声明 —— 机制只认
`observe`。

**护栏**：`test_prompt.t_query_observed` —— 查询类必须 `observe is True`，动作类
必须没有这个键。它挡的是"新加一个查询能力忘了写"这一类静默失效。

**要重启 moss**：`observe` 出厂声明是 `shell._scan_meta`（`on_created`）扫的，
重启日志里应当能看到
`observe verbs: car/map, car/status, car/stop, car/where, ...`。

**顺带报告（`prompt/protocol.md` 是用户手改文件，未代改）**：第 105 行
「清单里它那行带 ⟨observe⟩ 的」与第 193 行「清单里带 ⟨observe⟩ 的 routine」仍指向
已撤掉的标记。建议改成「出厂就声明了要结果的（查询类，如 car/status）/ 这类不用
加 observe="true"」—— 否则模型会去清单里找一个不存在的标记。

## moss 能看图：图从"回执载荷"进 prompt，只挂最近一拍（2026-10-25）

用户：「有一个 read_image 的 routine，让 moss 可以看到图，需要配置下，moss 这边
要适配下?」。要，而且要改三处 —— 那个 routine 在 xml agent 那套身体里能跑，
在 moss 里跑不通，也不是"注册一下"就行。

**它为什么在 moss 里跑不通**（两个硬点）：

1. `read_image.run` 里 `await self.call('agent/state', {'agent_id': ...})` 取项目
   根 —— moss 这套身体里没有 `agent/state`，调用直接抛"routine not registered"；
2. 它返回 `{'content': ..., 'images': [...]}`，而 moss 的回执载荷只认三键，
   `images` 会被 `frames.receipt` 当场判成"多了 images"→ 错误帧。

**改法（三处，各守一层）**：

| 层 | 改动 | 判据 |
|---|---|---|
| 回执契约 `frames.py` | 载荷加第四键 `images`（路径列表，只收 str 列表） | 图没法写成 `content` 那一句（base64 几十万字符）；`details` 那条"没有通往 prompt 的路"仍然成立 —— 它管的是随手塞的结构化数据，`images` 是显式声明的内容面 |
| 组装 `brain.py` | `input_items(..., supports_images=)` 把带图的 `<observe>` 变成 blocks（`input_text` + `input_image`） | 与 agent 侧同形状（`agents/_core/uploads.resolve_data_url` 转 data URL）；模型不吃图就一个块都不给 |
| 装配 `moss.yaml` | 注册 `routines/user/xml/read_image.py` + 词表 + `detail` | 词表名与注册必须成对；`detail` 点名才有参数子行（模型要绝对路径，这条得写在眼前） |

**`read_image` 的 agent 依赖改成"有身份才问"**：`agent_id and not project_root`
时才 `call('agent/state')`。moss 不注入 `from_agent_id`（也就没有"我是谁"），
相对路径按进程 cwd 解析。不是给旧写法兜底，所以**不 catch 异常** —— 真问错了要
看得见。另外补了 `__all__ = ['ReadImage']`（注册表只认 manifest，单文件条目靠它）
和 `meta['observe'] = True`（读图就是查询，看不见等于白读，与 car 族同一条）。

**图只挂最近一次带图的观测**，这是成本判据不是审美：moss 的历史是**全量重放**
（每拍把整条 `history_turns` 发给模型），一张 100KB 截图 base64 ≈ 13 万字符，
每拍付一遍，三拍就把窗口烧穿。图的语义本来就是"我正在看"而不是"我记得" ——
更早的观测只剩那行元数据（路径/类型/尺寸/字节数都在），要看就再读一次，便宜。

**回读不内联 base64**（`render_history(..., inline_images=False)`）：界面的 `[image]`
标记只需要块的位置，不需要数据。这条不是省事，是必须的 —— UI 的心跳**每秒**拉
一次 `recall`，真编码就是每秒把同一张图读一遍再转 13 万字符。所以
"回读与喂进去同一份" 的准确说法是**同一套块形态与取舍规则**，不是同一串字节。

**探针实测**（`deepseek/deepseek-flash`，`models.yaml` 里 `image: true`）：
responses 与 completions 两个协议都吃 `input_image` + data URL。踩到一个坑：
我随手用的那个 70 字节 1x1 PNG 是**坏图**（magic 对、内容坏），服务端 400
`unsupported image` —— 换手搓的合法 16x16 PNG 立刻通过。所以测试里的图用合法
字节：`read_image` 侧虽然有 `sniff_image` 看 magic，但**头对内容坏的图仍会 400**，
整拍失败。这条保留为已知边界（400 → stream 抛错 → 错误帧，不是静默）。

**UI**：`content` 变成 blocks 后，`transcript_text` / `history_text` 走
`_content_text()` —— 文本块照抄，图块换成一行 `[image]`。不做的话 `str(list)`
会把 13 万字符 base64 和一堆 Python 字典打上屏幕。

**测试**：`test_frames.t_shape`（images 收/空列表炸/坏形状炸/没图不放键）、
`test_brain.t_image_blocks`（blocks 形状、只挂最近一拍、读不出退回文本、回读不碰
磁盘）、`test_brain.t_read_image_flow`（端到端：真 `ReadImage` 类 → `<read_image/>`
→ 回执带图 → 下一拍 `input` 里有 `input_image`）、`test_prompt.t_query_observed`
（加 `ReadImage` 进查询类）、`test_ui.t_multimodal_content`（`[image]`、base64 不进
界面、历史页块名仍是"观测"）。回归：test_brain 10 / test_frames / test_prompt 12 /
test_ui 14 / test_receipts 全过；`check_yaml` 26 条目。

**要重启 moss**（`moss.yaml` 词表 + `brain`/`frames` 都动了）；UI 也重启（`ui.py`）。

---

## 连接只叫 close，不叫 stop（2026-10-30）

`hub.connect(kernel)` 的产物是连接，关闭连接自然叫 `close()`。Python / Rust /
Go / TypeScript 统一改完：公开 `Connection` 与底层 `Transport`（Rust 为内部
`GrpcClient`）都用 close 语义，应用、测试和注释同步，**不留 stop 兼容别名**。
Go 底层 `grpc.Server.Stop` 是第三方 API，不在改名范围。

这条不改变 routine 生命周期：`Kernel.stop()`、`Routine.stop()`、`handle.stop()`
仍然表示停止实体或命令实例；只有连接与传输的关闭改名。历史决定中的旧名保留原貌，
当前代码与现行文档不再使用 `Connection.stop`。

---

## 跨 hub 显式 stop 不是断连结算（2026-10-30）

`Kernel.stop()` 的“远程 hub 随断链自清，kernel 只收投影”曾被 `_stop_node`
无条件复用，导致正常 `handle.stop()`、reload 停旧实例和优先级抢占也不给存活
owner 发 `lifecycle.stop`：远端成为孤儿，父侧却收到 `DISCONNECTED`。

Python 增加 `during_shutdown` 并递归传递，只有 `Kernel.stop()` 开启；TypeScript
直接用既有 `_exiting` 限定同一分支。正常停止恢复为请求—确认：远程 owner 收
`lifecycle.stop`，回报前保留投影，最终透传 `STOPPED`；owner 已断线仍按
`DISCONNECTED` 本地结算。两侧 `kernel_stop` 均新增远程显式 stop 回归。

---

## 空闲 keepalive 由 kernel 放行（2026-10-30）

拨入端每 20s 发无数据 ping 是为了发现手机休眠、NAT 静默断链等半开连接，客户端
不能关掉这项能力。Python kernel 服务端同时设置
`grpc.keepalive_permit_without_calls=1` 与
`grpc.http2.min_recv_ping_interval_without_data=10000`：前者允许无活动 RPC 保活，
后者给 20s 周期留调度抖动余量；只开前者仍会撞默认 5min 接收门槛。
`max_pings_without_data` 保持默认 2，异常高频 ping 的滥用保护不关闭。

---

## `observe` 属装配，不属通用 routine（2026-11-01）

用户：「`routine/doc` 声明了 `'observe': True` —— 删掉，这是 moss 业务入侵通用
routine 了。」成立的判据不是"这个 unit 属于谁"，而是**这个键在描述谁**：
`observe` 说的是"这次调用要不要回执、要不要点灯叫下一拍"，那是身体编排的
判断（shell 的三侧合并），不是 routine 对自己能力的描述。

`routine/doc` 与 `read_image` 都在 `routines/user/routine`、`routines/user/xml`
下，xml agent 与 moss 共用同一份实现。它们在 `meta` 里带 `observe=True` 时，
一份跨身体的 unit 就记住了某一具身体的调用语法；换个装配，这个键没有任何
读者，却仍然出现在别人查到的文档里。

处理：

- 两处 `meta['observe']` 删除（`routine/doc.py`、`xml/read_image.py`），
  docstring 记一句"回执由调用它的身体声明"。
- `ShellInput` / `MossInput` 新增装配参数 `observe:`（名单）；`shell._apply_meta`
  把"routine 自己声明的"与"装配声明的"取并集，装配里写错的词启动时
  `log.error` 点名（同 `detail:` 的既有做法，静默失效等于查询白调）。
- `app/moss.yaml` 里声明 `observe: [routine/doc, read_image]` —— moss 的判断
  写在 moss 的清单里。

护栏：`test_prompt.t_query_observed` 断言通用件的 meta 里没有 `observe`、
且 `moss.yaml` 的装配名单齐全（漏一个就红）；`t_doc_returns_entry` 改为用
装配声明驱动，验证"声明 → 回执进下一拍"这条链没断。

代价：新装配要记得声明自己哪些词是查询类。这是刻意的 —— 宁可换身体时多写
一行，也不要让共用件替某具身体记住它的语法。

---

## rosbridge WebSocket 永远直连（2026-11-02）

`websockets.connect` 默认 `proxy=True`，会读取开发机的 `HTTP(S)_PROXY`。rosbridge
是 `ws://192.168.1.100:9090` 这类车内网地址，交给代理既违背拓扑，也会在代理提前
结束握手时触发 websockets 17.0.1 的二次 `InvalidStateError`。
`ros_client` 显式传 `proxy=None`，与 gRPC dial-in 禁用环境代理遵循同一边界；
公网 ASR/TTS 不因此失去代理能力。上游主分支已为代理握手 Future 增加 `done()`
防护，但不通过修改 site-packages 或跟踪未发布分支来修这条应用侧错误路径。

---

## Moss creator：注册与挂载是两件事（2026-11-03）

用户要给 Moss 一套类似 Pi `routine-creator` 的热更新能力，落点定为
`moss/dynamic/` + `prompt/creator`，复用现有 watcher，不另写文件监听与加载器。

关键边界：watcher 把类注册进 kernel，只保证 `call(name, ...)` 能路由；Moss 的 CTML
还有 Shell 词表这道闸门。因此 creator 的完成条件不是“文件保存”或“注册表出现”，而是
**注册 → reg 进当前词表 → 真实调用自验**。新增 `shell/registry` 只是 Shell.registry
控制面的 routine 薄桥，让 code 里的 `call` 能完成 reg/unreg/list；词表存储与 meta 重扫
仍只有 Shell 一份实现。

`prompt/creator` 排在 capability 前，教查重、pydantic schema、manifest 导出、轮询注册、
挂载、自验、同名迭代和卸载。dynamic 保留一个不进词表的 probe 作为非空 manifest 锚点。
能力清单仍遵守装配时快照，不因热注册自动重写系统前缀。

同时把运行入口残留的 `mossv2` 路径改到当前 `moss` 包：`app/moss.yaml`、
`routine/doc`、UI 与并行测试入口；否则清单预检和 prompt 测试根本无法导入。

验证：`app/moss.yaml` 29 条目、66 routines 全量加载；`test_prompt` 13 例全绿；
真实 in-proc 链路跑通 `code → shell/registry reg → dynamic call → CTML call`。

