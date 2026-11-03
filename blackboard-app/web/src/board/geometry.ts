import type { Pt, Seg } from "./types";

const fract = (x: number) => x - Math.floor(x);
export const rnd = (s: number) => fract(Math.sin(s * 127.1 + 311.7) * 43758.5453);

/** 三个不同周期的正弦叠加，近似平滑噪声。比引入 simplex 便宜，效果够用 */
export function noise1(s: number, seed: number): number {
  return (
    Math.sin(s * 1.7 + seed * 3.1) * 0.55 +
    Math.sin(s * 4.3 + seed * 7.7) * 0.3 +
    Math.sin(s * 9.1 + seed * 13.3) * 0.15
  );
}

export function sampleLine(
  x1: number, y1: number, x2: number, y2: number, n = 56,
): [number, number][] {
  const out: [number, number][] = [];
  for (let i = 0; i <= n; i++) {
    const s = i / n;
    out.push([x1 + (x2 - x1) * s, y1 + (y2 - y1) * s]);
  }
  return out;
}

export function sampleArc(
  cx: number, cy: number, rx: number, ry: number,
  a0: number, a1: number, n: number, seed: number,
): [number, number][] {
  const out: [number, number][] = [];
  for (let i = 0; i <= n; i++) {
    const s = i / n;
    const a = a0 + (a1 - a0) * s;
    const wob = 1 + noise1(s * 5, seed) * 0.018;
    out.push([cx + Math.cos(a) * rx * wob, cy + Math.sin(a) * ry * wob]);
  }
  return out;
}

/** 沿法线抖动：两端收敛，中段最明显。起落笔整齐，行笔自然歪 */
export function roughen(
  pts: [number, number][], amp: number, seed: number,
): [number, number][] {
  const out: [number, number][] = [];
  const last = pts.length - 1;
  for (let i = 0; i <= last; i++) {
    const s = i / last;
    const a = pts[Math.max(0, i - 1)];
    const b = pts[Math.min(last, i + 1)];
    let dx = b[0] - a[0];
    let dy = b[1] - a[1];
    const len = Math.hypot(dx, dy) || 1;
    dx /= len;
    dy /= len;
    const taper = Math.min(1, Math.sin(Math.PI * s) * 3.2);
    const o = noise1(s * 7, seed) * amp * taper;
    out.push([
      pts[i][0] - dy * o + noise1(s * 3.2, seed + 5) * amp * 0.25,
      pts[i][1] + dx * o + noise1(s * 2.4, seed + 9) * amp * 0.25,
    ]);
  }
  return out;
}

/** 压力曲线：起笔轻、中段重、收笔轻 */
export function pressurize(pts: [number, number][], base: number): Pt[] {
  const last = Math.max(1, pts.length - 1);
  return pts.map((p, i) => {
    const s = i / last;
    const w = base * (0.78 + 0.42 * Math.pow(Math.sin(Math.PI * s), 0.7));
    return [p[0], p[1], w] as Pt;
  });
}

/**
 * 按宽度量化切段，渲染时每段只需一个 lineWidth。
 *
 * 段内不重复前一段的末点（那是接缝，渲染时补），`from` 记下本段首点在原始
 * 点列里的位置 —— 进度只能按原始点算，否则末端会少画一截。
 */
export function toSegments(pts: Pt[], step = 0.7): Seg[] {
  const segs: Seg[] = [];
  let cur: Seg | null = null;
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i]!;
    const w = Math.max(1, Math.round(p[2] / step)) * step;
    if (!cur || cur.w !== w) {
      cur = { w, pts: [p], from: i };
      segs.push(cur);
    } else {
      cur.pts.push(p);
    }
  }
  return segs;
}

export function linePts(
  x1: number, y1: number, x2: number, y2: number, seed: number, base: number,
): Pt[] {
  const len = Math.hypot(x2 - x1, y2 - y1);
  const n = Math.max(12, Math.min(90, Math.round(len / 12)));
  const amp = Math.min(2.6, 0.6 + len * 0.0022);
  return pressurize(roughen(sampleLine(x1, y1, x2, y2, n), amp, seed), base);
}

/**
 * 强调用的手绘圈（`ring`）：不闭合，留 6~10% 缺口，起点略有偏移。
 * 它**故意**不是圆 —— 人强调某句话时是本子上一圈，不是画几何圆。
 */
export function circlePts(
  cx: number, cy: number, rx: number, ry: number, seed: number, base: number,
): Pt[] {
  const a0 = -Math.PI * 0.62 + rnd(seed) * 0.5;
  const sweep = Math.PI * 2 * (0.9 + rnd(seed + 3) * 0.05);
  return pressurize(roughen(sampleArc(cx, cy, rx, ry, a0, a0 + sweep, 150, seed), 2.2, seed + 11), base);
}

/**
 * 标准圆 / 椭圆（`circle` / `ellipse`）：闭合、等宽、抖动 < 1px。
 *
 * 与上头的区别不是"好看不好看"，而是**几何上是个圆**：半径恒定、首尾相接。
 * 采样从 12 点开始顺时针，接近人画圆的起手位置；抖动只做 0.4% 的半径微扰，
 * 肉眼看是齐的，但不会像 CAD 描出来那样死板。
 */
export function shapeCirclePts(
  cx: number, cy: number, rx: number, ry: number, seed: number, base: number,
): Pt[] {
  const n = Math.max(56, Math.min(240, Math.round(((rx + ry) / 2) * 1.1)));
  const raw: [number, number][] = [];
  for (let i = 0; i <= n; i++) {
    const s = i / n;
    const a = -Math.PI / 2 + Math.PI * 2 * s;
    const k = 1 + noise1(s * 4, seed) * 0.004;
    raw.push([cx + Math.cos(a) * rx * k, cy + Math.sin(a) * ry * k]);
  }
  return pressurizeEven(roughen(raw, 0.55, seed), base);
}

/** 箭头拆成三笔：杆 + 两个头。返回多段，调用方按顺序分配时间 */
export function arrowParts(
  x1: number, y1: number, x2: number, y2: number, seed: number, base: number,
): Pt[][] {
  const ang = Math.atan2(y2 - y1, x2 - x1);
  const head = Math.min(34, Math.max(16, Math.hypot(x2 - x1, y2 - y1) * 0.12));
  const spread = 0.42;
  const p1 = [x2 - Math.cos(ang - spread) * head, y2 - Math.sin(ang - spread) * head];
  const p2 = [x2 - Math.cos(ang + spread) * head, y2 - Math.sin(ang + spread) * head];
  return [
    linePts(x1, y1, x2, y2, seed, base),
    linePts(x2, y2, p1[0], p1[1], seed + 7, base * 0.9),
    linePts(x2, y2, p2[0], p2[1], seed + 13, base * 0.9),
  ];
}

/** 下划线：比直线多一点儿弧度，像是随手划的 */
export function underlinePts(
  x1: number, x2: number, y: number, seed: number, base: number,
): Pt[] {
  const n = 34;
  const raw: [number, number][] = [];
  for (let i = 0; i <= n; i++) {
    const s = i / n;
    raw.push([x1 + (x2 - x1) * s, y + Math.sin(s * Math.PI) * (2 + rnd(seed) * 2)]);
  }
  return pressurize(roughen(raw, 1.6, seed), base);
}

/**
 * 压力几乎均匀：多边形是一气呵成画完的，不该像单个短笔画那样两头细。
 * 只在首尾 8% 内轻微收笔 —— 否则起点和终点所在的角会凹下去，看着像断了。
 */
function pressurizeEven(pts: [number, number][], base: number): Pt[] {
  const last = Math.max(1, pts.length - 1);
  return pts.map((p, i) => {
    const s = i / last;
    const edge = Math.min(1, Math.min(s, 1 - s) / 0.08);
    return [p[0], p[1], base * (0.86 + 0.16 * edge)] as Pt;
  });
}

const quadAt = (
  p0: [number, number], p1: [number, number], p2: [number, number], t: number,
): [number, number] => {
  const u = 1 - t;
  return [
    u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
  ];
};

/**
 * 折线 / 多边形，**一整条笔迹**。
 *
 * 角上给一圈小圆角（二次贝塞尔），所以拐角处不会出现两笔对不上的缝——
 * 这是拼多条 line 做不到的：那是 N 笔，每笔各自两端收细，角上必然断。
 *
 * close=true 时**从首边中点起笔**，绕一圈后越过起点收笔：
 *   - 起笔落在边中间，不占掉一个角 —— 四个角才都能走圆角，不会有一个直角突出；
 *   - 收笔过冲一小截，起收笔变细的那两段互相压住，闭合处不会留白洞。
 * 别改回"停在起点前留缺口"：缺口正好在角上，看起来就是断线。
 */
export function polyPts(
  verts: [number, number][],
  seed: number,
  base: number,
  opts: { close?: boolean; radius?: number } = {},
): Pt[] {
  const vs: [number, number][] = verts.map((v) => [v[0], v[1]]);
  if (vs.length < 2) return [];

  let path = vs;
  if (opts.close && vs.length >= 3) {
    const a = vs[0]!;
    const b = vs[1]!;
    const mid: [number, number] = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const len = Math.hypot(dx, dy) || 1;
    const out = Math.min(len * 0.25, 5 + rnd(seed) * 4);
    path = [
      mid,
      ...vs.slice(1),
      vs[0]!,
      [mid[0] + (dx / len) * out, mid[1] + (dy / len) * out],
    ];
  }

  const n = path.length;
  const lens: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    lens.push(Math.hypot(path[i + 1]![0] - path[i]![0], path[i + 1]![1] - path[i]![1]));
  }

  const R = opts.radius ?? 9;
  // 首尾点落在边中间，本来就不需要圆角；中间每个角都圆
  const radAt = (i: number) => {
    if (i <= 0 || i >= n - 1) return 0;
    return Math.min(R, lens[i - 1]! * 0.45, lens[i]! * 0.45);
  };

  const raw: [number, number][] = [];
  const push = (p: [number, number]) => {
    const l = raw[raw.length - 1];
    if (!l || Math.hypot(p[0] - l[0], p[1] - l[1]) > 0.35) raw.push(p);
  };

  push(path[0]!);
  let prevEnd: [number, number] = path[0]!;

  for (let i = 0; i < n - 1; i++) {
    const a = path[i]!;
    const b = path[i + 1]!;
    const len = lens[i]!;
    if (len < 0.5) continue;

    const ux = (b[0] - a[0]) / len;
    const uy = (b[1] - a[1]) / len;
    const rOut = radAt(i);
    const rIn = radAt(i + 1);

    const start: [number, number] = [a[0] + ux * rOut, a[1] + uy * rOut];
    const end: [number, number] = [b[0] - ux * rIn, b[1] - uy * rIn];

    // 角：从上一段的落点经顶点弯到这个段的起点
    if (rOut > 0.5) {
      for (let s = 1; s <= 9; s++) push(quadAt(prevEnd, a, start, s / 9));
    }

    // 直边
    const straight = Math.hypot(end[0] - start[0], end[1] - start[1]);
    const steps = Math.max(2, Math.round(straight / 6));
    for (let s = 1; s <= steps; s++) {
      push([start[0] + (end[0] - start[0]) * (s / steps), start[1] + (end[1] - start[1]) * (s / steps)]);
    }
    prevEnd = end;
  }

  push(path[n - 1]!);

  // 多边形的抖动要小：角上抖多了会显得歪
  const amp = Math.min(1.5, 0.5 + raw.length * 0.004);
  return pressurizeEven(roughen(raw, amp, seed), base);
}
