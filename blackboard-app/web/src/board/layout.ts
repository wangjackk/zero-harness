import type { Anchor, Obj } from "@bb/shared";

const GAP = 0.022;

/**
 * 数值收口。
 *
 * 用户侧可能把数字写成字符串（`pad='26'`）。JS 的 `+` 碰到字符串会拼接，
 * 于是 `82 + "26"` 变成 `"82.26"`——pad 静默消失，圆贴在字上。
 * 这类错误不会抛，只会让形状悄悄变形，所以在入口转一次，别信传进来的类型。
 */
export function num(v: unknown, dflt: number): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : dflt;
}

export class Objects {
  private map = new Map<string, Obj>();

  add(o: Obj) {
    this.map.set(o.id, o);
  }
  get(id: string): Obj | undefined {
    return this.map.get(id);
  }
  remove(id: string) {
    this.map.delete(id);
  }
  list(): Obj[] {
    return [...this.map.values()];
  }
  clear() {
    this.map.clear();
  }
}

/** 待放内容的半宽半高（归一化）。有它，锚点返回的是"贴边点"；没有，返回的是文字基线 */
export interface Extent {
  hx: number;
  hy: number;
}

/**
 * 只做三件事：解引用、留间距、钳到画布内。不做自动排版。
 * 重叠是用户侧的责任——它每轮都能拿到对象表。
 *
 * 两种用法，区别在 `ext`：
 *   - 不传 `ext`（写字）：返回**基线**点，文字落在对象下方、与左边缘对齐。
 *   - 传 `ext`（图形）：返回**贴边**点，图形中心离对象正好隔一个 GAP + 自身半径，
 *     并且在另一轴上居中。否则 `below:h1` 会把圆心按基线偏下 0.86 个字高，
 *     圆整体沉到字下面去。
 */
export function resolveAnchor(a: Anchor, objs: Objects, ext?: Extent): [number, number] {
  if (Array.isArray(a)) {
    return [clamp(num(a[0], 0.5), 0.01, 0.99), clamp(num(a[1], 0.5), 0.06, 0.96)];
  }
  // 不是数组也不是字符串（比如传了个数字）：只能落到画布中央
  if (typeof a !== "string") return [0.5, 0.5];

  const idx = a.indexOf(":");
  // 没有冒号就不是锚点写法，别拿 slice(-1) 猜出个诡异坐标
  if (idx < 0) return [0.5, 0.5];

  const kind = a.slice(0, idx);
  const id = a.slice(idx + 1);
  const o = objs.get(id);
  if (!o) return [0.5, 0.5];

  const [x, y, w, h] = o.box;
  const cx = x + w / 2;
  const cy = y + h / 2;
  switch (kind) {
    case "right-of":
      return [
        clamp(x + w + GAP + (ext ? ext.hx : 0), 0.01, 0.99),
        clamp(ext ? cy : y + h * 0.86, 0.06, 0.96),
      ];
    case "left-of":
      return [
        clamp(x - GAP - (ext ? ext.hx : 0), 0.01, 0.99),
        clamp(ext ? cy : y + h * 0.86, 0.06, 0.96),
      ];
    case "above":
      return [
        clamp(ext ? cx : x, 0.01, 0.99),
        clamp(y - GAP - (ext ? ext.hy : 0), 0.06, 0.96),
      ];
    default:
      return [
        clamp(ext ? cx : x, 0.01, 0.99),
        clamp(y + h + GAP + (ext ? ext.hy : h * 0.86), 0.06, 0.96),
      ];
  }
}

function clamp(v: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, v));
}
