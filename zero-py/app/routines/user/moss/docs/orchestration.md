# 统一指令形态（编排契约）

> 2026-10-19 定稿。目标：**一条指令只有一种形态**——不要一处 magic、一处自解释。
> 判据来自用户既有实践（`app/routines/user/xml/`：`XmlRoutine` 各自解释自己的 body）。

## 一句话

**一个标签 = 一个实例 = 一个 body 解释器。** 实例把 body 解释成什么由它的类决定；
没有第二类"保留字指令"，没有解析器认识的魔法名。

## 三层

```
CTML 字节流
  │
  │ ① body 扫描：视野只一层（深度 1）
  ▼
实例层   标签 → 实例；body 字节流边到边投给实例；实例自己解释
  │        say  → body = 要说的话
  │        x    → body = 空
  │        all  → body = 一批子标签（它自己再扫一层）
  │
  │ ② 投递：verb 名 + lane 名寻址
  ▼
执行层   轨道：同 lane FIFO / 跨 lane 并行；扁平、按名寻址、全局共享
  │
  │ ③ 回执：完成即回流给发起者
  ▼
发起者    记账 / 继续等待 / 向上汇总
```

**实例层是树，执行层是平的。** 轨道永不成树。

## 四条统一规则

1. **标签即实例**：`<main:say>`、`<left:x/>`、`<all>` 走同一条路——create 实例、
   边到边转发 body、闭合发 eof。没有"容器走另一条路"。
2. **body 归实例**：父只负责切边界（扫到配对闭合为止），把字节原样转给子；
   子从零解释自己的 body。父不看子内部语法。
3. **body 扫描只往下钻一层**：`Text`（本层文本）/ `ChildOpen` / `ChildBody` / `ChildClose`。
   子内部（含再嵌套）整体作为 `ChildBody` 原样转发。没有 `SelfClose`/`Leftover`
   ——边界由父切，本层看不到自己的闭合标签。
4. **没有保留字**：`all` / `any` / `_` 是注册在词表里的**容器命令**，和 `say`、
   `chan_create` 平起平坐。解析器不认识它们；替换实现只需换注册类。

## 容器

容器就是实例，只是它的 body 解释方式是"把子标签投递成子实例、然后等"。

| 指令 | body 解释 | run 的等待语义 |
|---|---|---|
| `<all>` | 子标签逐个投递 | 等**全部**子实例完成 |
| `<any>` | 同上 | 等**任一**完成，其余**取消** |
| `<_>` / `<main:__scope__>` | 同上 | 只等**本 lane** 的子；一个都没有则等全部（原版 flow 容错） |
| `<loop times="N">` | 同上，且 body 原文留档 | 每轮等全部子完成，再重放 body；`N` 轮 / 失败 / 观测 / 硬顶 100 四个出口 |

`<left:all>` 合法：`all` 锚 `left` lane。量词不再需要"不得被通道名遮蔽"的特判。

容器属性 `timeout="3"`：到时不再等，取消剩余（与 `any` 的收尾同一套），回执带
`timed_out` 并**自带注意债**——超时是模型该看见的事实。

`loop` 是唯一"body 跑多次"的容器，也是统一形态最漂亮的延伸：**重放 body 原文**，
子标签重新 `route` 得到新实例、新 rid——"同一批指令跑 N 遍"不需要克隆、缓存或
特判任何执行态。终止条件照抄原版 `loop(times, ctml__)`：`times` 到 / 任一子失败 /
某子声明 `observe="true"` / 硬顶 100。收尾回执 `{content: '跑了 N 轮', details: {loops, cancelled}, observe: True}`，
对应原版 `CommandTaskResult(observe=True, "loop done at N")`。

**占道不需要任何新机制**：容器实例本身是 lane 队列里的一项，它的 `run` 要等子
完成才返回，于是同 lane 后续动作在 FIFO 上自然等它。这正是原版"scope exit 是
blocking 命令"的等价物——只是这里不需要闸，队列本身就是闸。

### 跨 lane 栅栏（"b 等 a 做完再开始"）

容器锚在**要等的 lane** 上，body 里只放对方 lane 的动作，自己 lane 的动作写在闭标签
之后：

```
<right:all>
<left:wave hand="left"/>
</right:all>
<right:grip/>          ← 等 left 落定才开始
```

读法：在 `right` 队列里插一项"等 left 的那个动作"。容器占住 `right` 直到 body 完成，
`grip` 排在它后面。要等多个动作就把它们都放进 body（同一 lane 的自然串行，跨 lane
的一起等）。

**反直觉但必须记住**：写在 body 里 ≠ 等在后面。`<right:all><left:wave/><right:grip/></right:all>`
是并行的——body 里的指令一被扫描到就投递，`grip` 不会因为写在 `wave` 后面而等它。

**闭标签握手**（`Container._queued` / `@request('ready')`）：容器延迟入队，所以
"收完 body"必须早于"后续同轨标签入队"成为**结构保证**而不是调度偶然。发起者在
`eof` 之后 `req(容器, 'ready')`，容器在 `release` 完成后应答；此后投递的同轨标签
必然排在容器后面。没有这声握手，`send(eof)` 不阻塞，后续标签可能抢在容器入队之前
（实测单组侥幸正确，多组连续时屏障时有时无）。

## 轨道管理：chan 族（create / status / clear / delete）

轨道是身体的并行结构，所以模型要开得出、看得到、清得掉、也**收得回**。四扇门都是
普通动作（同一个 `verb` 入口、同一个生命周期、同一条回执链），代码在 `commands/chan/`
一个目录里。

**分族判据是对象**：族内每个动作都针对**一条具体的轨**，都要 `name`：

| 动作 | 语义 |
|---|---|
| `<chan_create name="b"/>` | 开轨（幂等）；回执带全量轨名 |
| `<chan_status/>`、`<chan_status name="b"/>` | 每轨 `{queued, running, soon, deferred}`；省略 name = 全部轨 |
| `<chan_clear name="b"/>` | 丢掉 b 上排队的动作、停下正在跑的；轨本身还在，之后照常可投 |
| `<chan_delete name="b"/>` | 回收**空轨**；有活拒绝；`main` 不许删 |

`chan_status` 是唯一允许省 name 的：它读的就是整张表，一次查询看全量才是常态。

叫 `status` 而不是 `list`：它交付的不是"有哪些轨"（那点信息每条记录的 `chan`
字段就带出来了），而是**运行时状态**——排着几个、正在跑什么、有没有容器还在收 body。
名字三层同名：动作 `chan_status` → 控制面 `op='status'` → channel 自己的
`@request('status')`，中间没有翻译。

**`interrupt` 不在族里**：它的对象是"所有轨"（全停），没有 name 可说——它不是
某条轨的操作，是"停一切"。原版 `clear` 就此收掉，因为它的两种形态正好各归一边：
`<clear/>`（无参）跟 `<interrupt/>` 语义完全相同，两个名字指一件事；`<clear chan="b"/>`
是"清某条轨"，归族成 `chan_clear name="b"`。判别只剩一条：**说得出轨名就是
`chan_clear`，说不出就是 `interrupt`**。清空是即刻的，两者都带 `call_soon`（见下节）。

`delete` 只收空轨是机制而非洁癖：队列非空时 `channel.stop()` 会等在
`_jobs.join()` 上把活跑完——那是挂住调用方，不是回收。所以控制面先问 `status`，
有活当场报错并写下一步（先 `<chan_clear name="b"/>`）。

轨表存 **handle 而不是 id**（`shell._lanes`）：删轨要"谁启动谁停止"（`h.stop()`），
只留 id 就够不到实例。

## 其他标准指令（原版 `inject_system_primitives` 全表）

原版始终注入的 5 个标准原语，在这里都是普通指令，没有一个是新机制（`clear` 换了
名字归进 chan 族，语义没动）：

| 指令 | 类 | 语义 |
|---|---|---|
| `<sleep duration="1.5"/>` | 普通动作 | 睡；同 lane 后续动作排队等它。原版 `chan` 参数想表达的"在哪条轨阻塞"，由标签第一槽直接给出 |
| `<noop/>` | 普通动作 | 故意不作为。让"我选择沉默"与"没有输出"在账面上可区分 |
| `<chan_clear name="b"/>` | 控制 | 原版 `clear(chan="...")`，归族（对象是一条轨）：队列丢弃、在跑优雅停止 |
| `<interrupt/>` | 控制 | 全停所有轨——原版 `interrupt()`，也就是 `clear()` 的无参形态 |
| `<loop times="N">` | 容器 | 见上 |

`chan_clear` / `interrupt` 带 `call_soon`，理由见下一节。

## 调度属性：`call_soon`

`<chan_clear>` / `<interrupt>` 是"清空排程"的指令——排在排程里等前面动作跑完，它们就
什么也清不掉了。原版用 `PyCommand(..., call_soon=True)` 表达这件事，这里照抄成
**所有指令都能声明的 meta 属性**（不是给这两个名字开的旁路）：

```python
meta = {'call_soon': True}
```

channel 收到带此声明的实例时**不入队**，直接起跑（`_spawn`）。形态上没有分叉：
同一个 `verb` 入口、同一个实例生命周期、同一条回执链路；差别只在调度位置。
抢占型实例也不占 `_current_*`（那是串行语义的"在跑"槽位）。

**但它仍是一份活着的任务**，所以清轨/定点处死/轨收尾都必须能找到它：
`_spawn` 同时记 `_soon_handles: rid → handle`，`abort` / `kill` / `channel.stop`
对它逐个 `stop(fire=True)`。唯一的豁免是**发起清轨的那一个**——清道夫不清自己，
由发起者经 `shell.clear` 显式递 `except: [rid]`（不是靠 channel 猜名字）。

## `code`：body 是代码

`code` 是普通命令：同一个 verb 入口、同一个 body 协议、同一条回执链路，
唯一不同的是 body 是源码。它和容器（`all` / `any` / `loop`）一样"收完 body
才开工、`run` 等子、子汇总成一条账"，区别只在**子不是从 body 里扫出来的，
而是代码执行时直接建出来的**。

代码里没有轨道概念：`await` 本身就是排序，`gather` 就是并行，不需要轨道
记号来排。所以 `call` / `create` **直接建子实例**，不经过 `shell.route`，不进
lane 队列，也不产生根账条目——代码是个黑盒，账本上只有它自己那一条结果。
代价是容器在代码里没有意义（子要投给 `route` 才有轨可锚），`create` 遇到
容器名当场报错并指向 `gather`。

**执行在常驻内核里，不在 code 实例里**（`code_kernel.py`）。理由只有一条：
一份活着的 globals。一个标签 = 一个实例，实例里的 ns 随实例消失，等于每格
重启一次解释器；而"上一格定义的变量下一格还在"是 IPython 手感的一半。所以
`code` 只做三件事——收 body、转发、报账，源码送到 `CodeKernel` 执行。
内核 rid **由 code 自己按名字解析**（`_kernel_rid()`，与 `chan_create` /
`capability` 找 shell 同一套）：`route` 不为它开声明，也不注入参数——"要
什么宿主"是动作自己的依赖，不是投递方要当场知道的属性（对照 `observe`
/ `call_soon`：那两条改变的是"这条指令怎么进轨"，投递时必须知道）。

内核**一格一格跑**：`@request` 的 handler 是并发执行的（容器收帧正靠这一点），
而两个 cell 同时写同一份 ns 会互相覆盖（`x = x + 1` 丢更新），所以内核里有
一把锁——跨 lane 的 code 也排成一列。命名空间活到 moss 停：内核是 shell 的
子实例，shell 停则级联停。

**并行的那一半交给模块**：光有锁，两个格会在锁上**静默排队**——看起来像并行，
出事只有一句 `exec timeout`，看不出谁在等谁。所以 `code` 声明占用 `code`
模块（`modules.py` 的树，`kernel.create` 时建）：第二个格在 **start** 时被
kernel 的 `TryAcquire` 拒，错误帧当场回灌。排队还是报错，差别不在优雅，在于
模型这一拍能不能知道"一次只有一格"。

锁的范围是**一整格**（`async with self._lock` 圈住整个 `exec`），于是格里再叫
`code` 就是环等自己：外层握着锁等内层，内层等锁——一直等到外层超时，而回执照
样只有一句 `exec timeout`，看不出是自己等自己。所以 `create('code')` 当场拒绝
（`code cannot nest … write the source inline`）：嵌套一格与把源码写在同一格
**完全同义**（同一份 ns、同一把锁、同一个父），没有可换取的隔离。

同一件事的两种写法：

| CTML | 代码 |
|---|---|
| `<main:sleep duration="2"/>` | `await asyncio.sleep(2)` |
| `<main:all>x y</main:all>` | `await asyncio.gather(call('x'), call('y'))` |
| `<a:slow/>` `<a:x/>`（同轨先后） | `await call('slow')` 后接 `await call('x')` |
| `<main:loop times="3">x</loop>` | `for _ in range(3): await call('x')` |

**父必须比子活得久**，这是子实例挂谁名下唯一的判据。两种宿主各写各的：

- **格内 `await call(...)`** → 父是那一格的 `code`（内核代建时 `parent_id`
  指过去）。于是停止/打断照样级联（kernel `_stop_node` 先递归停子、再停
  自己）；若让子挂内核名下，"停 code"就会漏掉它正在跑的动作。
- **`spawn` 起的后台任务** → 父是内核。它存在的意义就是活过发起它的那次
  `exec`，而格一收尾节点就摘了——挂格名下，它醒来 `call` 只会拿到
  `parent routine N not found`（子比父长寿）。内核活到 moss 停，正好同生共死。

两种父由**建子实例那一刻的上下文**决定（`_parent_of` 这个 `ContextVar`）：
格内是格 id，`spawn` 启动前 `copy_context()` 后改成本类 id 再
`create_task(..., context=ctx)`——只改副本，格那份不动。这也让 `code` 不必
声明 `call_soon`：它 body 里的动作根本不在 lane 队列里，不存在"自己等自己"。

**执行形态**：继承 `CtmlRoutine`（所以 `{'text'}` 增量 + `{'eof'}` 收尾这两条
由基类管），两个钩子都不覆盖——就是"收完才执行"（源码必须完整）。
`ast.PyCF_ALLOW_TOP_LEVEL_AWAIT` 放行顶层 `await`（无需
`async def` + `asyncio.run`——后者在已有 loop 里直接报错）；末表达式改写成
`__result__ = <expr>` 取值，复刻 REPL 直觉（每格执行前先清掉上一格的，
防止上次的结果冒充这次）。

**代码面只有四个名字**（外加真模块 `asyncio`）：

| 名字 | 实现 | 说明 |
|---|---|---|
| `create(name, **kw)` | `self.create` + moss 侧挂 `feed`/`eof` | 拿句柄（created 态）：`start` / `feed` / `eof` / `stop` / `destroy` / `await h` |
| `call(name, **kw, body=None)` | `create` → `start` → 递 body → 等结果 | 失败抛 `CallError`，代码可捕获 |
| `publish(topic, data)` | `self.publish` | 旁路通知，不进账 |
| `print(...)` | 写入本格 `StringIO` | 随回执回灌（不动全局 stdout） |

**发起者账（`_owner_of`）**：框架只给一种父——建它的实例，没有"替调用方代建"
的口子。所以格内建出来的动作父都是内核，框架不会在格收尾时替它收；谁要的
这件事只有内核自己记。`_create` 把成功建出来的句柄记进 `_kids[发起者]`；
格收尾和任务收场（取消/抛错/自然结束）各自由 `_reap` 逐个 `stop`。少记一笔，
那一批实例就活到内核停。

**调用只有一种走法**：`create` 拿句柄自己驱动，或者 `call` 一步到底。没有
"某个 routine 有专用函数"这回事——`body` 是**消息面**而不是入参，`call` 收
`body=` 就等于闭标签：`await call('say', body='你好')` 与
`<main:say>你好</main:say>` 同义（`body` 因而成为 `call` 的保留参数名，与标签
体同名，不传给动作）。句中要改口就自己拿句柄：

```python
h = await create('say')
await h.start()
await h.feed('先说这一段')
await h.stop()          # 说到一半收口, words/stopped 在回执的 details 里
```

句柄上多出来的 `feed` / `eof` 由 moss 自己挂在实例上（`_attach_body`）：body
协议是 moss 的，框架的 `RoutineHandle` 只认 `send(任意数据)`，不为某个业务
多开一个口子。

等一会儿直接 `await asyncio.sleep(秒)`——不另给 `sleep` 别名：代码里没有轨道要占，
它只会是一个纯转发。

## body 的规范（给后面每一个"体带内容"的动作）

以后会有一堆 `<doc>…</doc>` / `<sql>…</sql>` / `<shell>…</shell>` 这类动作，
它们的共同点只有一条：**内容在 body 里，参数在属性里**。四个现有消费者
（`say` / `code` / 容器 / `loop`）的骨头已经收进 `CtmlRoutine`（`ctml.py`），
新动作只需继承它。规范就按这一条切：

- **判据**：kwargs 放参数（短、结构化、能枚举、能进 schema）；body 放内容
  （长、可能流式、可能就是源码或整篇文章）。同一个动作两种都收时，别让内容
  走参数——`call('doc', text='…')` 是错的写法。
- **作者侧**（写新动作：继承 `CtmlRoutine`，只回答两个问题）：
  1. `meta['body'] = 'body 是什么'`（一句话，如"要说的话"/"Python 源码"）——
     声明了才被允许喂 body；
  2. `on_body_chunk(text)`：这一段来了怎么办——**默认只攒着**（收完才开工）；
  3. `on_body_done()`：body 收完了怎么办——**默认什么都不做**。

  "边收边干"就覆盖 chunk（`say` 喂引擎、容器边扫边投），"收完才开工"就覆盖
  done（`code` 两个都不覆盖、`loop` 只在 done 里展开）。**协议不变，只是解释
  不同**——这正是 body 不特殊的全部内容。收流本身（两条消息、eof 幂等、打断
  放行、原文留档）都在 `CtmlRoutine` 里：`body_text` / `_body_ready` /
  `abort()`。新动作不该再抄一遍 `{'text'}` / `{'eof'}`。
- **调用侧**（写在代码里只有两种，没有第三种）：
  ```python
  await call('doc', format='md', body=md)     # 一次性: 内容整段给
  h = await create('doc')                     # 要中途插手才用句柄
  await h.start()
  await h.feed(md)                            # 可多段: 字符串/序列/生成器
  await h.eof()
  ```
  `body=` 也收异步生成器（边产边递，真流式）。别为每个动作造专用函数
  （`say()` 已按这条删掉）：body 是消息面，`call` 已经能表达它。
- **闸门**：`meta['body']` **默认是不收**，收 body 的类在自己的 meta 里写
  `'body': True`（不在基类里盖旗：meta 是机制的输入，机制读的键要在 meta 里
  看得见）；“body 里该写什么”进它自己的 `description`。不在旗里的动作收到
  体当场报错（`takes no body`，`call` 与句柄两条路都拦）。热挂的动作扫不到
  就不拦（不知道就不猜）。
- **可见性 / 提示词归属（2026-10-19，10-20 键名 `example`）**：能力清单
  只渲染**当前词表**里的 routine，每行由它自己的 meta 拼出，四层：
  `description`（一句话，含"body = 什么"）→ `input_schema`（签名 + 参数子行）→
  `example`（多行备注/陷阱）。
  **提示词跟能力在一起**：routine 没进词表，它那几行就不存在；`protocol.md`
  只讲与具体能力无关的语法与机制（标签形态、轨道、观测回灌、红线），不再硬
  编码 `say` / `code` / 容器 / `sleep` 这些具体名字。同一段 `example` 只印
  一次——容器族把共享结构（body 里放子 routine / 占道 / `timeout` / 跨轨栅栏）
  声明在各自 meta 里指向同一份常量，输出里不会重复四遍。
  （装配缺口：名字进了词表但 catalog 没注册 → 那行只剩裸名字，没有说明。
  它不报错，只是哑巴——所以词表与注册必须成对；`capability` 现在会为这种
  名字打一条 warning。`clear` 归族成 `chan_clear` 后忘了从词表撤掉，正是
  这么哑了几天。）

  **两级披露（2026-10-25）**：上面四层不是对每个名字都印。装配在清单的
  `detail:` 里点名的那批详列（签名 + 参数子行 + `example`），名单外的在
  所属分组末尾收成一行 `另有: a b c`（**只有名字**，见下条）。理由：前缀是
  每拍都在的常驻成本，而每拍都用得上的只有语法与轨。
  裸名项的用法没丢，在 `<routine/doc name="…"/>` 后面 —— 它与能力面共用
  `prompt/capability.entry()`，两处不可能漂移。`detail` 缺省（空）= 全详列
  并打一条 warning：没配就丢信息，比多花 token 更坏。

- **用语只有一个：`routine`（2026-10-20）**：万物皆 routine —— `shell` /
  `ghost` / `channel` / 容器 / 词表里的每个名字都是同一类东西，所以教学面
  **不许出现"动作 / 命令 / 指令"这第二种名词**。`commands/` 只是**分类
  目录**，标记"这些是给模型用的 routine"，不是另一个概念（`commands/__init__.py`
  的 docstring 写着这条）。
  理由不是文风：出现两个名词，模型就会以为存在两种调用方式、两套生命周期，
  于是去猜——猜得出写法，却猜不出语义。护栏 `test_prompt.t_one_noun` 扫
  `persona.md` / `protocol.md` 与 15 个 routine 的 meta 三元组，命中即红；
  同一处还守 meta 不示范调用语法（语法由 `protocol.md` 统一讲，且接入的
  routine 未必跑在同一前端）。本文档里"动作"一词若出现，指"一次 routine
  调用"，不是另一种东西。

**回执粒度是一个 cell 一条**，并且**出厂声明 `observe`**：body 里的调用不进
根账，这条回执就是 code 的唯一可见面（stdout / 末表达式 / 错误都只在这里），
不产债就等于算完没人看见。载荷 `content` 是 stdout 与末表达式（模型读这个），
`details: {stdout, result, ok}` 是原文。**代码跑挂不算门失败**（执行并报告结果
就是它的活），错误写进 `content` 并自带 `observe`；只有内核没起来才抛。

**语法错回执要连源行一起还**（2026-10-20）：CPython 的原话
（`invalid character '，' (U+FF0C) (line 1)`）之外，再附**出错那一行 + 指针**：

```
SyntaxError: invalid character '，' (U+FF0C) (line 1)
  import time，asyncio
             ^
```

理由不是好看：**body 不进回执，模型看不见自己刚发出去的源码**。只给行号，它
只能凭记忆重写刚才那段——于是"我是不是打了个全角逗号"变成猜谜，而猜错的
方向永远是怀疑宿主改过它的代码。原始行原样贴回去，这种争论当场结束。

**代价**（不隐藏）：代码内部的调用不在根账本里，模型只能从 cell 的
`stdout` / `error` 看到它们；也没有轨道的"物理通道串行"保护——同时 await
两个动作是真的并发（这正是 `gather` 的意义），模型要自己对同一通道的争用
负责。命名空间跨格累积、只增不减：同名变量互相覆盖、`del` 得自己记得，
这是 IPython 用户熟悉的那种疼；要彻底干净只能重启进程。

**已知边界**（不隐藏）：

1. eval 在本进程事件循环里跑，纯 CPU 的 cell（死循环、同步阻塞 IO）会冻住
   arena/TTS/ASR，`stop()` 也插不进去（取消不生效，只能等 STOP_TIMEOUT）。
   要根治得把内核换成独立进程——内核已经是"一个 hub 就能搬走"的形态：ns 在
   自己进程里，跨进程只传 `{cell, src}` 与回执；另需 kernel 的 owner 死亡
   级联（已落地，见 `routine/kernel/manager.py`）。
2. 一次**只有一格**：`code` 占 `code` 模块，第二个格在 start 就被拒（不是排队）。
   长格会独占这份资源，要并行就在格内 `gather`，不是多开格。
3. 回执粗：内部调用不产生根账条目，细账只在 cell 自己的 `stdout`/`error` 里。


## 回执与账本

- **谁投递谁收回执**：实例完成 → 回执发给它的发起者（父容器或 root shell）。
- 发起者收齐后向上汇总一层；**账本仍然只有 root 一本**（一条指令一条账）。
  一帧分两层：**信封** `{kind, verb, chan, rid, owed}` 说成没成、谁在哪条轨；
  **载荷** `result: {content, details, images?, observe?}` 说结果是什么。失败
  没有载荷，原因在信封的 `text` 上。
- **`images` 是载荷里唯一的非文本内容**（2026-10-25）：图没法写成 `content`
  那一句（一张图 base64 几十万字符），只能以多模态 block 附在消息上。读图类
  routine（`read_image`）交回本地路径列表，`brain.input_items` 转成
  `input_image` 块附在 `<observe>` 那条 user 消息上，且**只挂最近一次带图的
  观测**（历史全量重放，每拍都发一遍图三拍就把窗口烧穿）。`details` 仍然
  没有通往 prompt 的路 —— 那条判据管的是"随手塞进去的结构化数据"，`images`
  是显式声明的内容面。
- **完成由 `run` 侧判，不在回执处理器里置位**：处理器只「交货 + 报有一批到货」，
  容器在 `run` 里从到货清单标完成。否则 `run` 会抢在该请求的应答之前收摊，调用方
  收到的是 "stopped before replying"——帧明明已送到，却报丢。
- 拍边界（`close`）不等在途：指令漂着，回执迟到按旧拍号入史——这条语义不变。
- `owed`（注意债）仍盖在帧上，产地只有 root 记账处（`_record_trace`），四条来源
  or 合并：错误帧 / `observe="true"` 认领 / `meta.observe` 出厂声明 /
  执行结果自报 `{'observe': True}`（原版 `CommandTaskResult(observe=True)`，
  `loop` 收尾与容器超时用它）。

**三侧同名 `observe`**（2026-10-20）：标签属性、`meta['observe']`、结果自报的
`{'observe': True}` 是同一个词 —— 都是"这个结果需要被注意"，只是说的时机不同
（这一次 / 每次 / 结果自己说）。meta 的键不再叫 `always_observe`：`always` 只是
"每次"这层语境，meta 本身已经含了它，重复一遍反而让三处看起来像三个概念。

**为什么叫 `observe` 而不叫 `wait`**：原版 `wait_tasks(to_be_observed=True)` 是拍尾
**真等**观测类任务；我们把悬等撤了（拍该收就收，不等在途），只剩"结果到了点灯"。
名字沿用 `wait` 就是名不副实 —— 它不 wait 任何东西，也不让这一拍停下来（2026-10-20
改名）。meta 键与它同名，区别只在范围：一个说"这一次我要它的结果"，一个说"每次都要"。
值只认 `true`，写别的当场报错 —— 静默失效比报错难查。旧名 `wait=` 不设兼容：不当别名，
也不留护旧报错，它就是一次普通的不带 `observe` 的调用。

出厂声明是按**词表**算的（`registry` 只加名字），所以 reg/unreg 之后要重扫一次
（`_apply_meta`）。不重扫就是"名字能叫、声明不生效"——回执永远没债、清轨类永远
不插队，谁都不报错。扫不到（跨 hub 抖）**保持旧值**：清空等于把全部声明一起降级，
比不刷更糟。

## 错误归属

语法错误由**看见它的那一层**报：root 扫 body 时遇到坏标签 → root 的 error 帧；
容器扫自己的 body 时遇到坏标签 → 容器的 error 帧（写进它自己的回执）。
词表/lane 闸门只有一份（root 的注册表），容器投递时复用，不各抄一份。

错误帧一律带注意债（`owed`）——「话没落地」与远端动作失败同构，都是契约未履行，
所以当拍续一拍矫正（模型看见 error 换对写法，用户不必再喊一次）。债不在报错处盖：
`_frame_error` 只造一个 `kind='error'` 的帧交给报账，债由报账那**唯一一处**判定。

## 与旧形态的对照

| 旧 | 新 |
|---|---|
| `CtmlParser` 全局栈 + 保留字 scope token | `BodyScanner` 深度 1 + 闭合即边界 |
| `shell._dispatch` 里 `scope_open/scope_end` 特殊分支 | 无（容器是普通实例） |
| scope 无实例、靠 shell 记账 | 容器实例、自有 run 生命周期 |
| 只支持一部分标签有 body | 所有标签统一 body 通道 |

## 不变量（重构期间必须保持）

1. ghost 与 shell 的门面不变：`send({'text'})` / `{'close'|'abort', beat}` /
   `{'obs', beat}` 回灌。
2. 同 lane FIFO、跨 lane 并行。
3. 回执默认不驱动回声帧；`owed` 是唯一驱动（错误帧自带债，`observe` 出厂
   声明，`observe="true"` 调用期认领，结果自报 `observe`）。
4. 未建 lane / 未注册 verb 当场出 error 帧，带可用清单。
5. 流式 say 边到边吃 body，不因分层多一次缓冲。
