import type { AtomicDrawable, Drawable, GlyphDrawable, PathDrawable, Pt, Seg } from "./types";

export const INK = "#f2efe2";
export const FONT = '"KaiTi", "STKaiti", "Kaiti SC", "楷体", serif';

/**
 * 三层叠加代替 SVG 滤镜：主体 + 硬芯 + 粉尘。
 * feTurbulence 每帧重算会把帧率吃光，这是性能红线。
 */
const LAYERS = [
  { k: 1.0, a: 0.92, dx: 0, dy: 0 },
  { k: 0.42, a: 0.26, dx: 0.8, dy: 0.5 },
  { k: 1.9, a: 0.07, dx: -1.0, dy: -0.8 },
];

export class Renderer {
  readonly ctx: CanvasRenderingContext2D;
  private baked: HTMLCanvasElement;
  private bctx: CanvasRenderingContext2D;
  w = 1;
  h = 1;
  dpr = 1;
  /** 图片缓存：data URL / http 都走这里，避免每帧新建 Image */
  private imgs = new Map<string, HTMLImageElement | "error">();
  /** 图片加载完的通知：Board 用它重建一次位图，保证 ops 顺序 */
  onImage: ((src: string) => void) | null = null;

  constructor(private canvas: HTMLCanvasElement) {
    this.baked = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    const bctx = this.baked.getContext("2d");
    if (!ctx || !bctx) throw new Error("2d context unavailable");
    this.ctx = ctx;
    this.bctx = bctx;
    this.resize();
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.w = Math.max(1, rect.width);
    this.h = Math.max(1, rect.height);
    this.dpr = dpr;
    for (const c of [this.canvas, this.baked]) {
      c.width = Math.round(this.w * dpr);
      c.height = Math.round(this.h * dpr);
    }
    for (const c of [this.ctx, this.bctx]) {
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.lineCap = "round";
      c.lineJoin = "round";
    }
  }

  clear() {
    this.bctx.clearRect(0, 0, this.w, this.h);
  }

  /** 完成的笔迹固化进位图。注意：擦除是不可逆的，所以位图才是画面真相 */
  bake(d: Drawable, p = 1) {
    this.paint(this.bctx, d, p);
  }

  frame(now: number, active: Drawable[]) {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);
    ctx.drawImage(this.baked, 0, 0, this.w, this.h);
    for (const d of active) {
      if (now < d.t0) continue;
      const p = d.dur <= 0 ? 1 : Math.min(1, (now - d.t0) / d.dur);
      this.paint(ctx, d, p);
    }
  }

  private paint(ctx: CanvasRenderingContext2D, d: Drawable, p: number) {
    if (d.kind === "glyph") this.paintGlyph(ctx, d, p);
    else if (d.kind === "atomic") this.paintAtomic(ctx, d);
    else this.paintPath(ctx, d, p);
  }

  /**
   * 原子像素操作：直接落在目标画布上，没有入场动画。
   * 落在烘焙画布上是不可逆的（就像真黑板擦过），靠 Board 保留 ops 重放来还原。
   *
   * 坐标按**整像素外扩取整**：否则 fillRect 的边界像素是半透明覆盖，
   * clearRect 再按同一小数边界擦只会把覆盖比例相乘，留下一圈擦不掉的毛边。
   */
  private paintAtomic(ctx: CanvasRenderingContext2D, d: AtomicDrawable) {
    const W = this.w;
    const H = this.h;
    const snap = (x: number, y: number, w: number, h: number) => {
      const x0 = Math.floor(x * W);
      const y0 = Math.floor(y * H);
      const x1 = Math.ceil((x + w) * W);
      const y1 = Math.ceil((y + h) * H);
      return [x0, y0, Math.max(1, x1 - x0), Math.max(1, y1 - y0)] as const;
    };

    ctx.save();
    for (const op of d.ops) {
      if (op.op === "image") {
        const img = this.image(op.src);
        if (img) ctx.drawImage(img, op.x * W, op.y * H, op.w * W, op.h * H);
        continue;
      }
      if (op.op === "clearRect") {
        const [x, y, w, h] = snap(op.x, op.y, op.w, op.h);
        ctx.clearRect(x, y, w, h);
        continue;
      }
      ctx.fillStyle = op.color ?? INK;
      ctx.globalAlpha = op.alpha ?? 1;
      if (op.op === "pixel") {
        ctx.fillRect(Math.floor(op.x * W), Math.floor(op.y * H), 1, 1);
      } else {
        const [x, y, w, h] = snap(op.x, op.y, op.w, op.h);
        ctx.fillRect(x, y, w, h);
      }
    }
    ctx.restore();
  }

  /** 图片要异步加载；没加载完的那一帧就不画，加载完回调 Board 重建一次 */
  private image(src: string): HTMLImageElement | null {
    const hit = this.imgs.get(src);
    if (hit === "error") return null;
    if (hit) return hit.complete && hit.naturalWidth > 0 ? hit : null;

    const im = new Image();
    im.onload = () => {
      this.imgs.set(src, im);
      this.onImage?.(src);
    };
    im.onerror = () => this.imgs.set(src, "error");
    im.src = src;
    this.imgs.set(src, im);
    return null;
  }

  /**
   * 按**原始点下标**推进度。
   *
   * 段是按笔宽切出来的，一段画完不等于进度到点：这里用 seg.from 把"原始点位置"
   * 和"段内位置"分开算，末段才不会因为接缝补点被算多而整段丢失。
   */
  private paintPath(ctx: CanvasRenderingContext2D, d: PathDrawable, p: number) {
    if (d.count < 1) return;
    const target = d.count * p;
    if (target <= 0) return;

    for (const lay of LAYERS) {
      ctx.save();
      ctx.translate(lay.dx, lay.dy);
      ctx.globalAlpha = lay.a;
      ctx.strokeStyle = INK;
      let prev: Pt | undefined;
      for (const seg of d.segs) {
        if (target <= seg.from) break;
        const take = Math.min(seg.pts.length, target - seg.from);
        if (take > 0) this.strokeSeg(ctx, seg, take, lay.k, d.dash, prev);
        if (take < seg.pts.length) break; // 本段被截断，后面的段还没轮到
        prev = seg.pts[seg.pts.length - 1];
      }
      ctx.restore();
    }
  }

  private strokeSeg(
    ctx: CanvasRenderingContext2D,
    seg: Seg,
    take: number,
    k: number,
    dash?: boolean,
    prev?: Pt,
  ) {
    // 浮点的 take 要在最后一个整点之后插一个点，动画才不跳
    const full = Math.floor(take);
    const frac = take - full;
    const pts: Pt[] = seg.pts.slice(0, Math.min(seg.pts.length, Math.max(1, full)));
    if (frac > 0 && pts.length < seg.pts.length) {
      const a = seg.pts[pts.length - 1]!;
      const b = seg.pts[pts.length]!;
      pts.push([
        a[0] + (b[0] - a[0]) * frac,
        a[1] + (b[1] - a[1]) * frac,
        a[2] + (b[2] - a[2]) * frac,
      ]);
    }
    if (pts.length === 0) return;

    ctx.beginPath();
    ctx.lineWidth = Math.max(0.6, seg.w * k);
    ctx.setLineDash(dash ? [16, 11] : []);

    if (pts.length === 1) {
      // 只画到一个点：round cap 下零长度线段描不出来，给个极小位移当"落笔"
      const p0 = pts[0]!;
      ctx.moveTo(p0[0], p0[1]);
      ctx.lineTo(p0[0] + 0.02, p0[1]);
    } else if (prev) {
      // 接缝：从前一段的落点起，笔迹不会在段边界出现缝
      ctx.moveTo(prev[0], prev[1]);
      for (const p of pts) ctx.lineTo(p[0], p[1]);
    } else {
      ctx.moveTo(pts[0]![0], pts[0]![1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i]![0], pts[i]![1]);
    }
    ctx.stroke();
  }

  /** 文字揭示：clip 区域从左往右推，那就是"笔尖"的位置 */
  private paintGlyph(ctx: CanvasRenderingContext2D, d: GlyphDrawable, p: number) {
    if (p <= 0) return;
    const w = d.w * p;

    ctx.save();
    ctx.beginPath();
    ctx.rect(d.x - d.size * 0.18, d.y - d.size * 1.1, w + d.size * 0.18, d.size * 1.5);
    ctx.clip();
    ctx.translate(d.x, d.y);
    ctx.rotate(d.tilt);
    ctx.font = `${d.size}px ${FONT}`;
    ctx.textBaseline = "alphabetic";
    ctx.fillStyle = INK;
    ctx.globalAlpha = 0.94;
    ctx.fillText(d.ch, 0, 0);
    ctx.globalAlpha = 0.2;
    ctx.fillText(d.ch, 0.8, 0.6);
    ctx.restore();
  }
}
