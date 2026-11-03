import type { Obj, PixelOp } from "@bb/shared";

/** [x, y, 笔宽] —— 笔宽逐点携带，这是选 Canvas 而非 SVG 换来的东西 */
export type Pt = [number, number, number];

/** 连续的一段等宽笔画 */
export interface Seg {
  pts: Pt[];
  w: number;
  /**
   * 本段首点在**原始点列**里的下标。
   *
   * 入场进度必须按原始点算：段是按宽度切出来的，段与段之间的接缝靠额外补点，
   * 那些补点不属于笔迹本身。用补点算进度会把"画到哪"算多，末尾几段就永远画不出来。
   */
  from: number;
}

export interface PathDrawable {
  kind: "path";
  act: string;
  segs: Seg[];
  count: number;
  t0: number;
  dur: number;
  dash?: boolean;
}

export interface GlyphDrawable {
  kind: "glyph";
  act: string;
  ch: string;
  x: number;
  y: number;
  size: number;
  w: number;
  t0: number;
  dur: number;
  tilt: number;
}

/** 原子像素操作。dur 恒为 0：它没有入场，落到就生效 */
export interface AtomicDrawable {
  kind: "atomic";
  act: string;
  ops: PixelOp[];
  t0: number;
  dur: 0;
}

export type Drawable = PathDrawable | GlyphDrawable | AtomicDrawable;

export type { Obj };
