import { SIZE_RATIO, type Action, type Down } from "@bb/shared";
import { Objects, num, resolveAnchor } from "./layout";
import { Clock } from "./clock";
import { FONT, Renderer } from "./renderer";
import { arrowParts, circlePts, linePts, polyPts, rnd, shapeCirclePts, toSegments, underlinePts } from "./geometry";
import type { AtomicDrawable, Drawable, GlyphDrawable, PathDrawable, Pt } from "./types";

/** 写完多久固化进位图。过期笔迹必须离开每帧循环，这是帧率的命门 */
const BAKE_AFTER = 1.2;
/** 一个字的书写时长 */
const STEP = 0.155;

/**
 * 静态黑板：没有时间轴、没有队列、没有回放。
 *
 * 动作一到就画，各自跑自己的入场动画，画完就固化进位图。节奏由发送方控制
 * （用户侧想慢就分条发、两条之间自己等），黑板本身不替任何人排队。
 */
export class Board {
  readonly objects = new Objects();
  readonly clock = new Clock();
  readonly renderer: Renderer;

  /** 全部笔迹，是清屏/重建的日志 */
  private all: Drawable[] = [];
  /** 还没烘焙的，每帧重算 */
  private active: Drawable[] = [];
  private seed = 1;
  private last = 0;

  constructor(canvas: HTMLCanvasElement) {
    this.renderer = new Renderer(canvas);
    // 图片是异步的：加载完重画一遍，保证 ops 的先后顺序不被打乱
    this.renderer.onImage = () => this.rebuild();
  }

  get W() {
    return this.renderer.w;
  }
  get H() {
    return this.renderer.h;
  }

  resize() {
    this.renderer.resize();
    this.rebuild();
  }

  ingest(msg: Down) {
    switch (msg.type) {
      case "abort":
        // 插话：正在写的笔就地停住（留下的半笔就是被打断的痕迹）
        this.interrupt();
        break;
      case "actions":
        for (const it of msg.items) this.build(it.act, it.id);
        break;
      case "resync":
        this.objects.clear();
        for (const o of msg.objects) this.objects.add(o);
        break;
    }
  }

  start() {
    const loop = (t: number) => {
      const dt = this.last ? Math.min(0.05, (t - this.last) / 1000) : 0;
      this.last = t;
      this.clock.advance(dt);
      this.tick();
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  clear() {
    this.objects.clear();
    this.all = [];
    this.active = [];
    this.renderer.clear();
    this.renderer.frame(this.clock.now, this.active);
  }

  // ---------- 内部 ----------

  private tick() {
    const now = this.clock.now;
    let baked = false;
    const keep: Drawable[] = [];
    for (const d of this.active) {
      if (now - (d.t0 + d.dur) > BAKE_AFTER) {
        this.renderer.bake(d, 1);
        baked = true;
      } else {
        keep.push(d);
      }
    }
    if (baked) this.active = keep;
    this.renderer.frame(now, this.active);
  }

  /** 把在画的笔按最终形态固化。像素操作要看到"完整画面"才谈得上擦 */
  private flush() {
    for (const d of this.active) this.renderer.bake(d, 1);
    this.active = [];
  }

  /**
   * 从笔迹日志重建位图。原子像素操作也在日志里，所以 clearRect 也能被重放，
   * 擦除不会把别的对象的像素操作搞丢。
   *
   * 只用于擦除与尺寸变化：在画的笔一律按最终形态重画，z 序才不会乱。
   */
  private rebuild() {
    this.renderer.clear();
    this.active = [];
    for (const d of this.all) this.renderer.bake(d, 1);
    this.renderer.frame(this.clock.now, this.active);
  }

  /** 擦掉一个对象：它的全部笔迹离开日志，位图重建，对象表同步删掉 */
  private eraseObject(id: string) {
    if (!this.objects.get(id)) return; // 没有就什么都不做，erase 应当幂等
    this.objects.remove(id);
    this.all = this.all.filter((d) => d.act !== id);
    this.rebuild();
  }

  /** 把进行中的笔按当前进度定格进位图，其余丢掉 */
  private interrupt() {
    const now = this.clock.now;
    for (const d of this.active) {
      const p = d.dur <= 0 ? 1 : Math.min(1, Math.max(0, (now - d.t0) / d.dur));
      if (p > 0) this.renderer.bake(d, p);
    }
    this.active = [];
    this.renderer.frame(now, this.active);
  }

  private build(act: Action, id: string) {
    const W = this.W;
    const H = this.H;
    const ink = Math.max(2.2, Math.min(W, H) * 0.0042);
    /** 形状尺寸的唯一单位：画布短边。位置按轴归一化，长度不能按轴 —— 否则圆会变椭圆 */
    const u = Math.min(W, H);
    const t0 = this.clock.now;

    switch (act.do) {
      case "write":
        this.buildWrite(act, id, t0);
        break;

      case "line": {
        const [ax, ay] = resolveAnchor(act.from, this.objects);
        const [bx, by] = resolveAnchor(act.to, this.objects);
        const pts = linePts(ax * W, ay * H, bx * W, by * H, this.seed++, ink);
        this.pushPath(id, pts, t0, { dash: act.dash });
        this.registerStroke(id, pts, W, H);
        break;
      }

      case "arrow": {
        const [ax, ay] = resolveAnchor(act.from, this.objects);
        const [bx, by] = resolveAnchor(act.to, this.objects);
        const parts = arrowParts(ax * W, ay * H, bx * W, by * H, this.seed++, ink);
        for (const pts of parts) {
          const dur = Math.min(1.2, Math.max(0.16, pts.length * 0.006));
          const d: PathDrawable = {
            kind: "path",
            act: id,
            segs: toSegments(pts),
            count: pts.length,
            t0,
            dur,
          };
          this.all.push(d);
          this.active.push(d);
        }
        break;
      }

      case "circle": {
        // 标准圆：圆心 + 半径，半径按短边折算，所以宽屏上也是正圆
        if (act.at == null) {
          // 只给了 around 的是旧写法：报出来，别让它在画布正中画个圆糊弄过去
          console.warn("[board] circle 需要 at（圆心）+ r；要圈住某个对象请用 ring");
          break;
        }
        const r = Math.max(2, Math.abs(num(act.r, 0.08)) * u);
        // 传 ext：锚点变成"贴边点"，`below:h1` 的圆整个落在字下面而不是压在字上
        const [nx, ny] = resolveAnchor(act.at, this.objects, { hx: r / W, hy: r / H });
        const pts = shapeCirclePts(nx * W, ny * H, r, r, this.seed++, ink);
        this.pushPath(id, pts, t0);
        this.registerStroke(id, pts, W, H);
        break;
      }

      case "ellipse": {
        const rx = Math.max(2, Math.abs(num(act.rx, 0.08)) * u);
        const ry = Math.max(2, Math.abs(num(act.ry ?? act.rx, 0.08)) * u);
        const [nx, ny] = resolveAnchor(act.at, this.objects, { hx: rx / W, hy: ry / H });
        const pts = shapeCirclePts(nx * W, ny * H, rx, ry, this.seed++, ink);
        this.pushPath(id, pts, t0);
        this.registerStroke(id, pts, W, H);
        break;
      }

      case "ring": {
        // 强调圈：手绘、留缺口，围着对象转一圈。不是几何图形，别拿它画圆
        const o = this.objects.get(act.around);
        if (!o) {
          // 引用了不存在的 id 静默跳过会让人查半天，至少留一条日志
          console.warn(`[board] ring 引用不到对象 "${act.around}"，已跳过`);
          break;
        }
        const [bx, by, bw, bh] = o.box;
        // pad 必须是数字：字符串会让 (bw*W)/2 + pad 变成拼接，pad 就没了
        const pad = Math.max(0, num(act.pad, 14));
        const pts = circlePts(
          (bx + bw / 2) * W,
          (by + bh / 2) * H,
          (bw * W) / 2 + pad,
          (bh * H) / 2 + pad,
          this.seed++,
          ink,
        );
        this.pushPath(id, pts, t0);
        break;
      }

      case "rect": {
        // 一条笔迹绕一圈：从左上角顺时针，角上做微圆，收笔停在起点前一点
        const w = Math.abs(num(act.w, 0.2)) * u;
        const h = Math.abs(num(act.h, 0.2)) * u;
        // 锚点给的是矩形**中心**（贴边语义），再折回左上角
        const [nx, ny] = resolveAnchor(act.at, this.objects, { hx: w / 2 / W, hy: h / 2 / H });
        const x = nx * W - w / 2;
        const y = ny * H - h / 2;
        const verts: [number, number][] = [
          [x, y],
          [x + w, y],
          [x + w, y + h],
          [x, y + h],
        ];
        const pts = polyPts(verts, this.seed++, ink, {
          close: true,
          radius: Math.max(0, num(act.round, 7)),
        });
        this.pushPath(id, pts, t0);
        this.registerStroke(id, pts, W, H);
        break;
      }

      case "poly": {
        const verts: [number, number][] = [];
        const list = Array.isArray(act.points) ? act.points : [];
        for (const a of list) {
          const [nx, ny] = resolveAnchor(a, this.objects);
          verts.push([nx * W, ny * H]);
        }
        if (verts.length < 2) break;
        const pts = polyPts(verts, this.seed++, ink, {
          close: act.close,
          radius: Math.max(0, num(act.round, 7)),
        });
        this.pushPath(id, pts, t0);
        this.registerStroke(id, pts, W, H);
        break;
      }

      case "underline": {
        const o = this.objects.get(act.around);
        if (!o) break;
        const [bx, by, bw, bh] = o.box;
        const pts = underlinePts(
          bx * W,
          (bx + bw) * W,
          (by + bh) * H + Math.max(0, num(act.pad, 8)),
          this.seed++,
          ink,
        );
        this.pushPath(id, pts, t0);
        break;
      }

      case "point": {
        const [nx, ny] = resolveAnchor(act.at, this.objects);
        const pts = shapeCirclePts(nx * W, ny * H, 5.5, 5.5, this.seed++, ink);
        this.pushPath(id, pts, t0, { dur: 0.22 });
        break;
      }

      case "erase":
        if (act.around === "all") this.clear();
        else this.eraseObject(act.around);
        break;

      case "pixel": {
        // 先把手上的动画收紧，像素操作看到的必须是完整画面
        this.flush();
        const d: AtomicDrawable = { kind: "atomic", act: id, ops: act.ops, t0, dur: 0 };
        this.all.push(d);
        this.renderer.bake(d, 1);
        this.renderer.frame(this.clock.now, this.active);
        break;
      }
    }
  }

  private buildWrite(act: Extract<Action, { do: "write" }>, id: string, t0: number) {
    const W = this.W;
    const H = this.H;
    // size 传了不认识的档位时退回 m，别让 SIZE_RATIO 拿到 undefined 算出 NaN 字号
    const size = Math.round(Math.min(W, H) * (SIZE_RATIO[act.size ?? "m"] ?? SIZE_RATIO.m));
    const text = typeof act.text === "string" ? act.text : String(act.text ?? "");

    const [nx, ny] = resolveAnchor(act.at, this.objects);
    const x = nx * W;
    const base = ny * H;

    // 宽度必须在真实字体下量，后端算不出来 —— 这也是对象表只能在前端生成的原因
    const ctx = this.renderer.ctx;
    ctx.save();
    ctx.font = `${size}px ${FONT}`;
    const chars = [...text];
    const widths = chars.map((c) => ctx.measureText(c).width);
    // 上下界按**墨迹**量，不按字号估：估算会把 descender 那截空也包进来，
    // 框整体偏低，圈的上弧就压在笔画上、下面反而空一块。
    const m = ctx.measureText(text);
    ctx.restore();

    const inkAsc = m.actualBoundingBoxAscent > 0 ? m.actualBoundingBoxAscent : size * 0.86;
    const inkDesc = m.actualBoundingBoxDescent > 0 ? m.actualBoundingBoxDescent : size * 0.18;

    const totalW = widths.reduce((a, b) => a + b, 0);

    let cx = x;
    chars.forEach((ch, i) => {
      const cw = widths[i]!;
      if (ch.trim()) {
        const g: GlyphDrawable = {
          kind: "glyph",
          act: id,
          ch,
          x: cx,
          y: base,
          size,
          w: cw,
          t0: t0 + i * STEP,
          dur: STEP * 1.45,
          tilt: (rnd(this.seed * 7 + i) - 0.5) * 0.04,
        };
        this.all.push(g);
        this.active.push(g);
      }
      cx += cw;
    });

    this.objects.add({
      id,
      kind: "text",
      text,
      box: [x / W, (base - inkAsc) / H, totalW / W, (inkAsc + inkDesc) / H],
    });
  }

  private pushPath(act: string, pts: Pt[], t0: number, opts: { dash?: boolean; dur?: number } = {}) {
    const dur = opts.dur ?? Math.min(1.6, Math.max(0.3, pts.length * 0.0075));
    const d: PathDrawable = {
      kind: "path",
      act,
      segs: toSegments(pts),
      count: pts.length,
      t0,
      dur,
      dash: opts.dash,
    };
    this.all.push(d);
    this.active.push(d);
  }

  private registerStroke(act: string, pts: Pt[], W: number, H: number) {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const p of pts) {
      if (p[0] < minX) minX = p[0];
      if (p[0] > maxX) maxX = p[0];
      if (p[1] < minY) minY = p[1];
      if (p[1] > maxY) maxY = p[1];
    }
    this.objects.add({
      id: act,
      kind: "stroke",
      box: [minX / W, minY / H, (maxX - minX) / W, (maxY - minY) / H],
    });
  }
}
