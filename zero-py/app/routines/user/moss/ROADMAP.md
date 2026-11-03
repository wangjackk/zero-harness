# moss 路线

MOSShell 的按自己理解的重建, 不是移植. 目标: CTML-first 的最小对话体.

**这份文件只回答两件事: 往哪走, 走到哪了.** 为什么每个岔口选那边(含被推翻的
前一版)、落地时踩了什么细节, 在 `docs/decisions.md`; 机制怎么跑在
`docs/orchestration.md`. 三处不重复 —— 路线会变, 依据要留.

## 现在是什么

六个部件, 一句话一个(细节见 orchestration):

- **Ghost 灵魂**: 一拍 = 输入入记忆 → 脑子产字流 → 观测回灌记忆. 记忆是感知
  关键帧(Moment)的有界滑窗; 脑子是外设(`brain_factory` 注入).
- **Mindflow 桥梁**: 裁判循环 + 检录缓冲 + 多源仲裁(拉取时打). 何时想归它.
- **Shell 身体**: 统一指令形态 —— 深度 1 切边界, 一个标签 = 一个实例, 实例自己
  解释自己的 body; 一本总账, 回执带 owed.
- **Channel 轨**: 队列 + 单消费者, 同轨先后 / 分轨并行. 它是纯调度器.
- **Moss 一体**: 装配器官 + 词表 + prompt 片段, 接通即放手.
- **交互面**: 内核 dial-in + 订阅渲染(`ui.py`), UI 与灵魂互不认识.

## 走到哪了

- **2026-10-15 骨架**: CTML 解析 + 分发闭环 + Ghost/Shell/Moss 分层 + 真脑接入
  (首轮即合法 CTML). 八核审计定版, interrupt/knock/command 三缺口同轮补齐.
- **2026-10-16 仲裁与回灌**: 多源仲裁照抄(challenge 体系 + 挂账不丢) → 仲裁
  时序对齐(拉取时打) → 推送制回灌(总账一本挂 shell). 回灌哲学四轮推翻, 落成
  **指令结果不触发**: 落账 ≠ 激活, 只有 owed 点灯才续拍. 打断走生命周期原语.
- **2026-10-17 注意债 + 交互面**: `owed` 一帧一戳(错误自带债 / `always_observe`
  / `wait="observed"` 三源合流); 错误是回声的一种; 终端退役, UI 进程外挂
  (dial-in + 订阅).
- **2026-10-18 耳朵与治理**: ASR 自实现器官(豆包流式) + 原版礼仪层(11 坐标);
  nucleus 恢复为**源治理单元**(不是时间轴); UI 帧链侧栏 / 自动重连定音.
- **2026-10-19 统一指令形态**: 容器不再是 magic(解析器不认识任何名字),
  `all/any/loop` 与动作同一条投递链; 5 原语齐; `code`(body 是源码) 落成,
  执行在常驻内核、命名空间跨格; 能力提示词跟着能力走.
- **2026-10-20 收敛**: 万物皆 routine(用语单一名词)/ 术语回到 body / chan 族
  四门 / `code` 成包 + 占模块(并行当场拒) / 能力名片前端中立且示例双侧对照 /
  `wait="observed"` 改名 `observe="true"`(名字终于对齐行为: 它只点灯, 不 wait),
  三侧(标签属性 / meta 键 / 结果自报)统一成 `observe`; 旧名不设兼容 /
  ptt 段边界归"松手"(判停三刀让位), 静音计时改由 partial 也展期;
  装配默认改成 `ptt`(按住说话、松手交付), 识别会话重启后的 partial
  按"改字/续接"合并(不再丢前半句).
- **2026-10-21 长出黑板**: `board/` 接一块**静态**黑板(Socket.IO 用户侧),
  `commands/board/` 十三扇门(十二个 do + 一眼; `bb_write` / `bb_pixel` 收
  body, 其余全属性)、每笔记完自己那一笔的动画时长再回执; 惰性连接,
  服务端没起也不拖垮装配. 接口升到十二个 do 后 `circle` 是几何圆、
  批注圈叫 `ring`, 长度按画布短边.
- **2026-10-21 抢占的判据是"有东西可停"**: 在途拍一个字还没投出去时
  (首字延迟实测 8.3s), 普通输入不再抢占, 排队成下一届 —— 原来是补一句
  "?" 就把整拍打没了, 两条消息一起白等. 显式 `interrupt` / FATAL 照旧
  必赢(knock 也不排队: 它整个身份就是可丢).
- **2026-10-21 思考看得见了**: 脑子把 reasoning delta 交给灵魂挂的
  `on_reasoning`, 灵魂只广播(`moss/think`), UI 渲染成灰斜体 "想: " 行,
  正文一到就收 —— 首字那几秒不再是空白. 思考不进身体(防草稿变命令)、
  不进 logos(草稿不是承诺).
- **2026-10-21 回执瘦身 + 上下文护栏**: `bb_objects` 只回清单(id/kind/
  文字开头), 几何要 `box="true"` 且截到千分位; `brain._frame_line` 每条
  回执行 600 字上限 —— 观测帧入史仍全量, 截断只发生在喂脑子那一行.
- **2026-10-21 拍外通知 `notify`**: `declare` 的门面, 缺省
  `BACKGROUND + mode=notify` —— **永不抢擂**(有现任必输)且输了挂账, 所以
  "空闲时立刻开一拍 / 说话时说完再答"是同一组参数长出来的两种结果, 门不必
  判断链在不在场. `mode=interrupt` 才停身体(自带 `effort='normal'`, 否则被
  primitive 补成停而不答). 出厂核 +1(`notify`, 不聚合).
- **2026-10-21 后台任务有主了**: `code` 里等外界不用建 routine ——
  `spawn(协程, name=…, desc=…)` 起一条跨格任务, 停不停归模型(`t.cancel()`)。
  父是**内核**不是那一格(格收尾节点就摘, 子比父长寿会 `parent N not found`),
  格内 `await call` 才挂格; 取消连带收掉它派出的实例。内核只做三件非它不可的事: 收摊时统一取消(`stop`)、
  死讯进**下一格回执**(asyncio 那句 "never retrieved" 只有 stderr, 模型看不
  见)、账上写清谁在跑。回执照实报
  `运行中 N 条: 名字(在等什么)` —— name 对号, desc 说清是什么, 同名自动 `#2`。
- **2026-10-21 回执分两层**: 信封(成没成/谁在跑/要不要点灯)× 载荷
  `{content, details, observe}` —— `content` 是模型读的那一行, `details`
  给人看(UI/记忆), **没有通往 prompt 的路**. 600 字截断随之退居兜底
  (  4000), 因为"整张表灌进上下文"已经无路可走. 失败不再写进载荷: 门做不到
  就抛, 信封改成 error.
- **2026-10-21 装配清单化**: moss 的 routine 名单搬进 `app/moss.yaml`(比
  `routines.yaml` 多一段 `modules:` 身体树), 唯一入口 `python -m app.main
  app/moss.yaml` —— 名单不再写死在代码里, 装配顺序也只有一处。词表/prompt
  片段随 kwargs 流入。`say` 合成一个(有 key 真声, 没有就 silent),
  脑子由 Moss 装配时插上。`moss/run.py` 删除: 两个入口 = 两套装配顺序,
  等价性靠人维护迟早分叉。
- **2026-10-21 灵魂不在时只报一条**: 失败后先问运行表(`get_running_routines`
  按名+id), 目标已停就**不再发 interrupt** —— 那是给不存在的目标补刀, 只会把
  一条故障打成两条 traceback; 同时把 `灵魂不在了(ghost N 已停), 需要重启 moss`
  推到 UI。待定: 装配件(ghost/shell/mindflow)被裸 reload 打成"无人认领"
  ---- 要么 reload 不动别人组装的实例, 要么装配者负责重建(会失忆)。

- **2026-10-21 耳朵换源（方案定稿，代码未动）**: 自带 SAUC 直连改成总线下游
  （micserver / asrserver / orchserver），ptt 用现成的 `manual`；常开麦要
  orch-rs 补一个 `always` 策略（Policy + `vad_idle` 三处派生）外加输入闸门
  `orch.cmd mute_input`（aec 已弃用，orch 里那套 aec 订阅与 `tts_playing` 字段一并删掉）
  与 VAD 自愈。我们侧走 provider（`sauc | bus`），一行环境变量切回。
  交接单：`E:\code\pyfiles\sherpa-onnx\services\orch-rs\docs\moss-always.md`
  （双方契约 + `BusEar` 骨架 + 验收）。判据：**段边界一律由持有音频的一侧给**，
  判停层（silence/classifier/keywords）整体退位。另：`always` ≠ barge-in ——
  每条 `result` 只是一条普通输入，打断由礼仪声明、回应由脑子决定（交接单 §8 划界）。

- **2026-10-21 提示词骨架对齐原版**: `protocol.md` 按原版 CTML 元指令
  (`core/ctml/prompts/v1_0_0.zh.md`) 重排 —— 三条原则(签名即能力/时间第一公民/
  结构化并发)、使用思路四步、红线、最佳实践、内联陷阱; 照搬**在我们机制里成立**
  的部分, 砍掉没有对位物的(通道树与 occupy、`@observe/@macro` 装饰器、
  `text__/chunks__/ctml__`、`_cid`、类型后缀与 CDATA)。能力面补 `⟨observe⟩`
  标记(原版装饰器同构, `t_observe_marker`)。
- **2026-10-21 保留字符**: 属性值与标签外正文还原 XML 实体
  (`&lt;` `&gt;` `&amp;` `&quot;` `&apos;` + 数字实体; 必须带分号, 认不出的原样 ——
  `R&D` 不受影响), body 里加 `<![CDATA[…]]>` 保护区(标记剥掉、内容字面、里面的
  `</say>` 不算闭合)。顺带修掉"顶层孤立 `<` 吞掉后面整段文本"—— `1 < 2 对吧`
  现在完整, 不再误报 unterminated; 流尾半截标签也吐出而不是丢掉。
  **容器 body 不解码**: `&lt;` 在子体里原样给下一层, 否则 `<all>&lt;x/&gt;</all>` 会变成
  执行(注入); 要念 `<` 用 CDATA。CDATA 保护内容、不保护容器 body 里的标签(照实写)。
- **2026-10-22 叶子 body 解码**: 上一版"body 一律不解码"把 `<code>` 也绕进去了 ——
  实测 `if abs(x-0.5) &lt;= 0.48` 字面进 Python, 当场 `SyntaxError`。现在按身份分:
  叶子(`say` 的话 / `code` 的源码 / 黑板要写的字)还原实体, 容器不还原。
  `body.decode_body` 带片边界留尾(`&#65` 到了 `;` 还没来就扣住), 两处转发口同源。
  提示词因此能主动推荐 `&lt;`: `a<b` 会被当标签开头一直等到 `>`, `a&lt;b` 绝无歧义。
- **2026-10-21 语法差距盘点**(只读结论, 详见 `decisions.md` 同日节): 原版 CTML 相对
  我们仍缺类型后缀 `arg:int=`、值 `literal_eval` 兜底(list/dict 参数写不进来)、
  `_cid`、`@macro`; 通道树与 `_args` 是刻意不补(平轨 / 与"全具名 + 当场拒"冲突)。
- **2026-10-21 写错当场拒**: 参数名不在签名里 / 给没有 `⟨body⟩` 的 routine 塞
  内容, 原来都是 pydantic 静默丢弃 —— 不报错、不生效, 模型以为做了, 然后自己
  编一个结果。现在投递口按 `input_schema.properties` 白名单拒并列出它到底收
  什么(`shell._check_args` / `_deliver_body`), 代码面同一道闸抛 `CallError`
  (`code_kernel._check_kwargs`)。容器补 `ContainerInput`/`LoopInput`, 让
  `timeout`/`times` 从"暗参数"变成清单上看得见的签名。

- **2026-10-25 交互面能看上下文**: UI 侧栏从"Moment 帧链"扩成三页签(帧链/
  历史/系统提示词), 一份 `recall` 供三处 —— 另开两个拉取口就是两套时序, 翻页
  时对不上. 历史页显示 `model_input`: 脑子**自己报**的"这一拍读到的原样"
  (`_input_items` 提成模块函数 `input_items()`, `Brain.render_history` 供
  回读; 脑子不提供就是空表), UI 不重拼块形态; 系统提示词页是 `recall.system`
  原文(装配拼一次的那份, 每拍随请求发). 长文页切过去才排、内容没变不重建
  (否则每来一条回执就把读长文的人弹回顶部); 载荷键名收在 `context_payload()`
  一处(少字段是静默的, 键名分两处写 = 改一边忘一边, 症状只是"页签空白").

- **2026-10-25 清单不印标记**: `⟨body⟩` / `⟨observe⟩` 两个括注撤掉(只印名字与
  说明) —— 它们想通告的机制两头都不在清单上(body 怎么写看它自己 description,
  这一次要不要结果看标签属性), 而同一条事实每拍都付一遍. 同一天把 `meta['body']`
  **翻成默认不收**(收的类自己写 True): 漏写"不收"是响的(当场一句"内容只能写进
  参数"), 漏写"收"是静默 —— 默认要落在不会被忽略的那一侧; 而且 meta 是机制的
  输入, 机制读的键该在 meta 里看得见, 不靠继承推导.

- **2026-10-25 听来的话带"听得多准"**: `<listen>` 补上原版那三个属性
  (`low_conf` 低置信字 / `db` 这一段多响 / `paused` 是否收尾). 原料本来就有
  (逐字 `conf`、每块 `rms_db`、阈值 `deliver.low_confidence`), 断在耳朵只传
  text、`Impulse` 没位置. 一条链补齐: `EarEvent.words` + `level_db()` 探针 →
  controller 段内采样/段尾结账 → `Impulse` 三字段 → `_percept_attrs` 按源挂 →
  `Moment.percept_attrs` → `brain._listen`. `paused` 只在 `false` 时印(恒真的
  属性只是每拍付字数), 空值不占位. (`low_conf`/`db` 当晚按用户要求撤出渲染,
  数据照旧在流 —— 恢复就是在 `_listen` 加两个分支.)

- **2026-10-25 能看图**: `read_image` 进装配, 图从**回执载荷**进 prompt ——
  载荷加第四键 `images`(路径列表), `brain._with_images` 转成 `input_image` 块
  附在 `<observe>` 那条 user 消息上(与 agent 侧同形状). 只挂**最近一次**带图的
  观测(历史全量重放, 每拍发一遍图三拍就烧穿窗口; 图是"我正在看"不是"我记得"),
  回读不内联 base64(`render_history(inline_images=False)`, UI 心跳每秒拉一次).
  模型不吃图(`models.yaml` 没写 `image: true`)就一个块都不给; 顺带给 `read_image`
  补了 `__all__` 与出厂 `observe`, 并把它的 `agent/state` 依赖改成"有身份才问"
  (moss 没有 agent, 相对路径按 cwd).

- **2026-10-30 连接关闭统一为 close**: py/rs/go/ts 的 `Connection` 与传输层都由
  `stop` 改名 `close`, 调用点与测试同步, 不留兼容别名; routine / kernel / handle
  的生命周期 `stop` 不变.
- **2026-10-30 跨 hub 显式 stop 修复**: py/ts kernel 仅在实际关停时直接结算
  远程投影; 正常 handle.stop / reload / 抢占向存活 owner 发 `lifecycle.stop`
  并等待 `STOPPED`, 不再误报 `DISCONNECTED` 或留下远端孤儿.
- **2026-10-30 空闲 keepalive 修复**: py kernel 允许无活动 RPC ping, 接收间隔门槛
  降为 10s 以容纳客户端 20s 保活; 保留默认高频 ping 限制, 不再每分钟 GOAWAY.
- **2026-11-02 rosbridge 强制直连**: WebSocket 显式 `proxy=None`, 车内网地址不再
  经 HTTP(S)_PROXY; 避免代理握手 EOF 触发 websockets 的二次 InvalidStateError.
- **2026-11-03 creator 热更新**: 新增 `moss/dynamic` 实验场与 `prompt/creator`;
  watcher 负责注册/重载, `shell/registry` 薄桥把新能力挂进当前词表, 真实调用自验后才交付.

## 没做(按远近)

- **L1 机身**: 语音双通已在(声声/耳朵); 剩动作映射(锥树 → 执行器)、心跳事件源
  (定时/感知源入泵 —— 泵与瞬态治理已就位, 申报口 `notify` 与"等的主体"
  (`code.spawn`) 也都有了; 还差**长期订阅做成独立 routine**(它比临时任务多
  一张门票: 要活过内核、要能被别的消费者看见))、epoch 自动触发.
- **执行期提权心跳**: 原版把现任强度钉平台, 我们拿保护期当替身 —— 保护期后拍内
  仍可衰减翻盘. 补它会改仲裁语义, 待定夺.
- **签名说全**: 现在只印参数名与默认值, 不印类型/取值(`asr_mode(mode='')` 看不出
  `always|ptt`). 要改得 `Literal` + `_sig_line` 读 `enum` 两处一起动.
- **code 内核搬独立进程**: 纯 CPU 的格仍冻主进程; 内核已是「一个 hub 就能搬走」
  的形态.
- **`chan_delete` 的 force**: 现在只收空轨; 加 force = 先清再删(静默丢结果的代价).

## 测试

```
cd zero-py; $env:PYTHONPATH='routine-py'
python -m app.routines.user.moss.tests.test_ctml       # 5 case (body 基类契约)
python -m app.routines.user.moss.tests.test_body       # 8 case (深度 1 扫描器)
python -m app.routines.user.moss.tests.test_dispatch   # 80 case (并行 x8: tests.run_parallel)
python -m app.routines.user.moss.tests.test_prompt     # 13 case
python -m app.routines.user.moss.tests.test_mutex      # 4 case
python -m app.routines.user.moss.tests.test_listen     # 5 case
python -m app.routines.user.moss.tests.test_keys       # 4 case (键盘后端; pynput 缺失时跳 2 例)
python -m app.routines.user.moss.tests.test_ui         # 14 case (交互面渲染 + 上下文三页; offscreen)
python -m app.routines.user.moss.tests.test_ui_think   # 3 case (思考渲染; offscreen)
uv run python -m app.routines.user.moss.tests.test_brain   # 10 case (含看图: 图块/端到端; 依赖 agents/_core)
```

判据不是"跑绿": 每个用例的名字本身就是一条约束(见 `tests/` 的 docstring 与
`docs/decisions.md` 里对应的决定).
