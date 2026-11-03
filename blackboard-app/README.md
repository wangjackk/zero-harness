# 黑板

一块静态黑板：**用户侧**说写什么、指哪个，**黑板侧**负责画出来。两侧通过 socket.io 通信，协议见 `docs/socketio-api.md`。

## 跑起来

```bash
bun install
bun run dev          # 同时起 server(3001) 和 web(5173)
```

打开 http://localhost:5173 ，是一块**空白板**——没人推动作就什么都不画。想看渲染效果：`bun run dev:demo`（内置演示流，用户侧一连上就自动让位）。

**用户侧怎么控制黑板，看 [`docs/socketio-api.md`](docs/socketio-api.md)**——握手、三个事件、十二个动作、坐标规则、坑，全在里面。用任意语言按那份线协议实现即可，服务端不用改。

```bash
bun run check         # vue-tsc 类型检查
bun run verify        # 无头浏览器读 canvas 像素：write / erase / pixel 逐阶段验证
bun run verify:shapes # 形状几何：正圆像素宽高相等、正方形四角、ring 外扩均衡、字上圆下不压字
bun run verify:roles  # 两侧角色分流：谁能推、谁被丢、对象表转给谁
bun run shot          # 画一屏对照并导出 shot.png（不开浏览器也能看效果）
```

## 三层数据

```
Action   用户侧说的：写什么、指哪个，不含坐标
Obj      布局后登记的对象，回灌给用户侧的就是它
Drawable 前端算出来的：点列 + 逐点笔宽 + 起止时间
```

`Action` 是 discriminated union，加一个 `do` 前后端同时报未处理分支。

## 两条硬约束

**位图是主状态，笔迹日志是还原依据。** 语义动作画进位图后不回滚；`erase` 靠"从日志里删掉该对象的笔迹、整块重建"实现，`pixel` 的 ops 也在日志里、重建时按序重放。所以位图是画面真相，日志是它能被擦除/重建的原因。

**过期笔迹必须离开每帧循环。** 写完 1.2 秒后固化进位图并从活跃列表移除（`board.ts: BAKE_AFTER`）。不这么做，几十秒后帧率就掉。

**黑板没有时间轴。** 动作到达即画，各自跑自己的入场动画。不排队、不暂停、不拖回放——节奏由发送方分条控制。`abort` 只定格正在画的那一笔。

**圆和圈是两个动作。** `circle` 是标准几何圆（圆心 + 半径，闭合等宽，像素上宽高相等）；`ring` 是强调用的手绘圈（围着某个对象、留缺口、带抖动）。拿 `ring` 当圆画一定不圆，拿 `circle` 圈重点又圈不住话——所以不合并。

**图形尺寸用画布短边做单位。** 位置按 x/y 分轴归一化没问题，长度不行：按轴归一化时 `w:0.3, h:0.3` 在宽屏上就是长方形，圆会被压成椭圆。

**像素操作要对齐整像素。** `fillRect` 的边界像素是半透明覆盖，`clearRect` 按同一小数边界擦只会把覆盖比例相乘，留一圈擦不掉的毛边（实测差 718 像素）。所以 `pixel` 的矩形先向外取整到整像素。

## 模块

| 文件 | 职责 |
|---|---|
| `shared/protocol.ts` | 前后端共用协议 |
| `server/src/index.ts` | socket.io、viewer/user 角色分流、seq、abort、对象表转发 |
| `server/src/script.ts` | 演示动作流（默认关，`BB_DEMO=1` 才跑） |
| `web/src/board/geometry.ts` | 手抖、压力曲线、线/圆/箭头、整条笔迹的折线与多边形 |
| `web/src/board/layout.ts` | 锚点解析、对象表 |
| `web/src/board/renderer.ts` | 双层 Canvas、粉笔三层描边、原子像素操作 |
| `web/src/board/clock.ts` | 一个只往前走的时钟（没有排期、没有回放） |
| `web/src/board/board.ts` | 门面：动作 → Drawable |
| `verify.ts` / `verify-shapes.ts` / `verify-roles.ts` | 三组端到端验证（像素 / 几何 / 角色） |
| `shot.ts` | 画一屏对照并导出 `shot.png` |

Vue 只管外壳。`Board` 是普通 class，没进响应式——60fps 渲染循环和响应式是两个时钟，混在一起必然互拖。

## 还没做

- 对象表：`arrow` / `ring` / `underline` / `point` / `pixel` 都不登记，事后引用不到
- `pixel` 的图片：跨域会污染画布，只能用 data URL；大图解码那一下会掉帧
- 节奏控制：没有背压机制，全靠发送方分条 + 等 `bb:objects`
- 文字轮廓描边：现在用 clip 揭示，不是沿字形一笔一划
