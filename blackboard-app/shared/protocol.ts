/**
 * 黑板侧与用户侧共用的协议。
 *
 * 两层角色，不涉及任何业务：
 *   黑板侧（viewer）—— 负责画，量得出 bbox
 *   用户侧（user）  —— 负责说写什么、指哪个，谁用都行（人、模型、脚本）
 *
 * 三层数据在这里交汇：
 *   Action —— 用户侧说"做什么、指哪个"，不含坐标
 *   Obj    —— 布局后登记的对象，回灌给用户侧的就是它
 *   Down/Up —— socket.io 上跑的消息
 *
 * 改这里会同时约束两侧：Action 加一个 do，黑板侧的 switch 立刻报未处理分支。
 */

/** 坐标系：归一化 [0..1] 相对画布，或锚点引用某个对象 */
export type Anchor =
  | [number, number]
  | `right-of:${string}`
  | `left-of:${string}`
  | `above:${string}`
  | `below:${string}`;

export type Size = "s" | "m" | "l";

/**
 * 原子像素操作：直接改位图，没有动画、没有语义。
 * 坐标同样是归一化 [0..1]，黑板侧按当前画布尺寸换算成像素。
 * ops 按数组顺序执行 —— 顺序有意义（先 clear 再 fill）。
 */
export type PixelOp =
  | { op: "clearRect"; x: number; y: number; w: number; h: number }
  | { op: "fillRect"; x: number; y: number; w: number; h: number; color?: string; alpha?: number }
  | { op: "pixel"; x: number; y: number; color?: string; alpha?: number }
  | { op: "image"; x: number; y: number; w: number; h: number; src: string };

/**
 * 两种量，两个单位 —— 这是最容易画歪的地方，先说清楚：
 *
 *   位置 —— 归一化 [0..1]，x 按画布宽、y 按画布高。位置按轴分是天经地义的。
 *   长度 —— 以**画布短边**为单位。`r / rx / ry / w / h` 全是长度。
 *
 * 长度绝不能按轴归一化：那样 `w:0.3, h:0.3` 在宽屏上就是个长方形，
 * 圆会变成横着压扁的椭圆。短边做单位，才能保证"圆是圆、方是方"。
 *
 * 凡是"图形"都走**一整条笔迹**：rect / poly / circle / ellipse 都是连续的一笔。
 * 不要用多条 line 去拼形状——那是多笔，每笔各自两端收细，接缝处一定看得出断。
 */
export type Action =
  | { do: "write"; text: string; at: Anchor; size?: Size }
  | { do: "line"; from: Anchor; to: Anchor; dash?: boolean }
  | { do: "arrow"; from: Anchor; to: Anchor }
  /** 标准圆：`at` 是圆心，`r` 是半径（短边单位）。闭合、等宽，画出来一定是正圆 */
  | { do: "circle"; at: Anchor; r: number }
  /** 标准椭圆：圆心 + 两轴半径（短边单位）。省略 ry 就是正圆 */
  | { do: "ellipse"; at: Anchor; rx: number; ry?: number }
  /**
   * 强调用的手绘圈：圈住一个已有对象。留缺口、带抖动、贴合字迹墨迹。
   * **这不是几何圆**——画一个圆请用 `circle`。两者不要混用同一个名字，
   * 否则"圈住这句"和"画个圆"在接口上就分不开了。
   */
  | { do: "ring"; around: string; pad?: number }
  /** 标准矩形：`at` 是左上角，w/h 为短边单位（w 与 h 相等就是正方形） */
  | { do: "rect"; at: Anchor; w: number; h: number; round?: number }
  /** 折线/多边形。`points` 至少两个点；close=true 首尾相连（留小缺口） */
  | { do: "poly"; points: Anchor[]; close?: boolean; round?: number }
  | { do: "underline"; around: string; pad?: number }
  | { do: "point"; at: Anchor }
  | { do: "erase"; around: string | "all" }
  | { do: "pixel"; ops: PixelOp[] };

/** 黑板上真实存在的东西。用户侧靠它知道"刚才写了什么"。 */
export interface Obj {
  id: string;
  kind: "text" | "stroke";
  /** 文字对象才有，是给用户侧看的摘要 */
  text?: string;
  /** 归一化 bbox [x, y, w, h]，前端量出来的 */
  box: [number, number, number, number];
}

/**
 * 握手 auth 里的角色。不填 = viewer（黑板侧）。
 * 只有 user 能推动作 —— 这不是权限，是"谁在说、谁在画"的分工。
 */
export type Role = "user" | "viewer";

/** 服务端 → 黑板侧 */
export type Down =
  | {
      type: "actions";
      seq: number;
      items: { act: Action; id: string }[];
      /** 与语音对齐：这句话的音频起止（秒）。黑板侧只做映射不做预测 */
      sync?: { start: number; end: number };
    }
  | { type: "abort"; seq: number }
  | { type: "resync"; seq: number; objects: Obj[] };

/**
 * 上行（两侧共用一条通道，按 type 分流）。
 *
 * input / abort / objects 来自黑板侧；
 * actions 只能来自用户侧 —— 服务端给它盖 seq 后广播给所有 viewer。
 */
export type Up =
  | { type: "actions"; items: { act: Action; id: string }[]; sync?: { start: number; end: number } }
  | { type: "input"; text: string }
  | { type: "abort" }
  | { type: "objects"; seq: number; objects: Obj[] };

/** 服务端 → 用户侧：某一块黑板的快照。`from` 是那块黑板的连接 id */
export interface ObjSnapshot {
  type: "objects";
  /** 哪块黑板报的。多块黑板同时在线时，只认你要控制的那块 */
  from: string;
  seq: number;
  objects: Obj[];
}

export const EV = {
  /** 服务端 → 黑板侧：动作流、打断、对象表重置 */
  down: "bb:down",
  /** 上行：黑板侧发 input/abort/objects，用户侧发 actions */
  up: "bb:up",
  /** 服务端 → 用户侧：黑板侧量出来的对象表 */
  objects: "bb:objects",
} as const;

/** 文字尺寸档位，相对于画布短边 */
export const SIZE_RATIO: Record<Size, number> = { s: 0.038, m: 0.055, l: 0.078 };
