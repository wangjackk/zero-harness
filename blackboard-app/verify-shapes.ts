/**
 * 验证图形完整性：末端有没有画到、角上有没有缝、圆是不是**圆的**。
 *
 * 两条判据不靠肉眼：
 *   1. 拿笔迹的实际 bbox 跟理论 bbox 比 —— 修复前每次笔迹末端会丢 10~19% 的点，
 *      表现在 bbox 的一边明显缩进去。
 *   2. 正圆的 bbox 在**像素**上必须宽高相等。按轴归一化算半径时它是个椭圆，
 *      这条断言当场就挂。
 */
import { io } from "socket.io-client";
import { EV, type Action, type Obj, type Up } from "@bb/shared";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const CPORT = 9334;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const user = io("http://localhost:3001", { transports: ["websocket"], auth: { role: "user" } });
await new Promise<void>((r) => user.on("connect", () => r()));
let lastObjects: Obj[] = [];
user.on(EV.objects, (m: any) => {
  lastObjects = m.objects;
});
const push = (act: Action, id: string) =>
  user.emit(EV.up, { type: "actions", items: [{ id, act }] } satisfies Up);

const profile = `D:/kshell/zero-harness/blackboard-app/.edge-${Date.now()}`;
const proc = Bun.spawn({
  cmd: [
    EDGE,
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--hide-scrollbars",
    "--no-first-run",
    `--remote-debugging-port=${CPORT}`,
    `--user-data-dir=${profile}`,
    "--window-size=1400,900",
    "http://localhost:5173/",
  ],
  stdout: "ignore",
  stderr: "ignore",
});

async function target(): Promise<any> {
  for (let i = 0; i < 30; i++) {
    try {
      const j = await (await fetch(`http://localhost:${CPORT}/json/list`)).json();
      const p = Array.isArray(j)
        ? j.find((t: any) => t.type === "page" && String(t.url).includes("5173"))
        : null;
      if (p) return p;
    } catch {}
    await sleep(400);
  }
  throw new Error("no CDP target");
}
const page = await target();
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise<void>((res, rej) => {
  ws.onopen = () => res();
  ws.onerror = (e) => rej(new Error("ws " + String(e)));
});

const pending = new Map<number, (v: any) => void>();
const errors: string[] = [];
ws.onmessage = (ev) => {
  const m = JSON.parse(String(ev.data));
  if (m.id && pending.has(m.id)) {
    pending.get(m.id)!(m);
    pending.delete(m.id);
    return;
  }
  if (m.method === "Runtime.exceptionThrown")
    errors.push(m.params?.exceptionDetails?.exception?.description ?? "exception");
};
const call = (method: string, params: any = {}) => {
  const id = Math.floor(Math.random() * 1e9);
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise<any>((res) => pending.set(id, res));
};
const evaluate = async (expression: string) =>
  (await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }))
    .result?.result?.value;

await call("Runtime.enable");
await call("Page.enable");

for (let i = 0; i < 40; i++) {
  const id = await evaluate(`window.__bbBoardId || ""`);
  if (id) break;
  await sleep(300);
}
await sleep(1500);

/**
 * 一次读回：不透明像素的 bbox（归一化）+ 指定窗口内是否有像素。
 * 窗口是归一化坐标，按 CSS 尺寸换算，内部再乘 dpr 到设备像素。
 */
const probe = async (points: [number, number][]) =>
  JSON.parse(
    await evaluate(`(() => {
      const c = document.querySelector('canvas');
      const dpr = c.width / c.clientWidth;
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      const W = c.width, H = c.height;
      let n = 0, x0 = 1e9, y0 = 1e9, x1 = -1, y1 = -1;
      for (let i = 3; i < d.length; i += 4) {
        if (d[i] <= 12) continue;
        n++;
        const p = (i - 3) / 4, x = p % W, y = (p / W) | 0;
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
      const RAD = Math.round(9 * dpr);
      const hits = ${JSON.stringify(points)}.map(([nx, ny]) => {
        const cx = Math.round(nx * W), cy = Math.round(ny * H);
        for (let y = Math.max(0, cy - RAD); y <= Math.min(H - 1, cy + RAD); y++) {
          for (let x = Math.max(0, cx - RAD); x <= Math.min(W - 1, cx + RAD); x++) {
            if (d[(y * W + x) * 4 + 3] > 12) return 1;
          }
        }
        return 0;
      });
      return JSON.stringify({
        n,
        box: n ? [x0 / W, y0 / H, x1 / W, y1 / H] : [0, 0, 0, 0],
        hits,
        cw: c.clientWidth,
        ch: c.clientHeight,
      });
    })()`),
  );

const reset = () => push({ do: "erase", around: "all" }, "c" + Math.random().toString(36).slice(2));

/** 只统计 y 大于等于 yFrom（归一化）的像素的 bbox —— 用来把图形从文字里分离出来 */
const probeBox = async (yFrom: number) =>
  JSON.parse(
    await evaluate(`(() => {
      const c = document.querySelector('canvas');
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      const W = c.width, H = c.height, y0p = Math.round(${yFrom} * H);
      let n = 0, x0 = 1e9, y0 = 1e9, x1 = -1, y1 = -1;
      for (let y = Math.max(0, y0p); y < H; y++) {
        for (let x = 0; x < W; x++) {
          if (d[(y * W + x) * 4 + 3] <= 12) continue;
          n++;
          if (x < x0) x0 = x; if (x > x1) x1 = x;
          if (y < y0) y0 = y; if (y > y1) y1 = y;
        }
      }
      return JSON.stringify({ n, box: n ? [x0 / W, y0 / H, x1 / W, y1 / H] : [0, 0, 0, 0] });
    })()`),
  );

// 画布尺寸：形状长度按短边折算，判据得跟着换算
await reset();
await sleep(400);
const base = await probe([]);
const CW: number = base.cw;
const CH: number = base.ch;
const U = Math.min(CW, CH);
/** 短边单位 → 归一化 */
const ux = (v: number) => (v * U) / CW;
const uy = (v: number) => (v * U) / CH;

// ---------- 1. 一条横线：末端必须到达终点 ----------
await reset();
await sleep(400);
push({ do: "line", from: [0.2, 0.5], to: [0.6, 0.5] }, "L1");
await sleep(2600);
const line = await probe([]);

// ---------- 2. 矩形：四角都要在，w===h 必须是正方形 ----------
await reset();
await sleep(400);
const RW = 0.3;
const RH = 0.3;
push({ do: "rect", at: [0.4, 0.42], w: RW, h: RH }, "R1");
await sleep(3200);
// at 是矩形中心
const rectL = 0.4 - ux(RW / 2);
const rectR = 0.4 + ux(RW / 2);
const rectT = 0.42 - uy(RH / 2);
const rectB = 0.42 + uy(RH / 2);
const rect = await probe([
  [rectL, rectT],
  [rectR, rectT],
  [rectR, rectB],
  [rectL, rectB],
]);

// ---------- 3. 三角形：三个顶点都要在 ----------
await reset();
await sleep(400);
push(
  {
    do: "poly",
    points: [
      [0.5, 0.16],
      [0.32, 0.52],
      [0.68, 0.52],
    ],
    close: true,
  },
  "T1",
);
await sleep(3200);
const tri = await probe([
  [0.5, 0.16],
  [0.32, 0.52],
  [0.68, 0.52],
]);

// ---------- 4. 正圆：像素上必须宽高相等 + 八向都有笔 ----------
await reset();
await sleep(400);
push({ do: "circle", at: [0.5, 0.42], r: 0.2 }, "C1");
await sleep(3200);
const circleRing: [number, number][] = [];
for (let k = 0; k < 8; k++) {
  const a = (k / 8) * Math.PI * 2;
  circleRing.push([0.5 + Math.cos(a) * ux(0.2), 0.42 + Math.sin(a) * uy(0.2)]);
}
const circ = await probe(circleRing);

// ---------- 5. 椭圆：两轴半径不同，但轴上的点要在 ----------
await reset();
await sleep(400);
push({ do: "ellipse", at: [0.5, 0.42], rx: 0.22, ry: 0.14 }, "E1");
await sleep(3200);
const ell = await probe([
  [0.5 - ux(0.22), 0.42],
  [0.5 + ux(0.22), 0.42],
  [0.5, 0.42 - uy(0.14)],
  [0.5, 0.42 + uy(0.14)],
]);

// ---------- 6. ring：pad 传字符串也要真的外扩，且上下均衡 ----------
await reset();
await sleep(700);
push({ do: "write", text: "圆形", at: [0.17, 0.42], size: "m" }, "h1");
await sleep(2200);
const textBox = lastObjects.find((o) => o.id === "h1")?.box;
// 故意传字符串：Python 侧 kwargs 很容易送成 '26'
push({ do: "ring", around: "h1", pad: "26" as any }, "k1");
await sleep(2400);
const ringed = await probe([]);

// ---------- 7. 字在上、圆在下：circle at:"below:h1" 不能压在字上 ----------
await reset();
await sleep(700);
push({ do: "write", text: "圆形", at: [0.17, 0.42], size: "m" }, "h2");
await sleep(2200);
const box2 = lastObjects.find((o) => o.id === "h2")?.box;
push({ do: "circle", at: "below:h2", r: 0.09 }, "c2");
await sleep(2600);
const box2After = lastObjects.find((o) => o.id === "h2")?.box ?? box2;
const textBottom2 = box2After ? box2After[1] + box2After[3] : 0;
// 只在字下方取 bbox，否则量到的是"字 + 圆"的并集
const under = await probeBox(textBottom2);
const textBottom = textBottom2;
const textCx = box2After ? box2After[0] + box2After[2] / 2 : NaN;
const circCx = (under.box[0] + under.box[2]) / 2;
const gapUnder = (under.box[1] - textBottom) * CH;
const centered = Math.abs(circCx - textCx) * CW;
const underPxW = (under.box[2] - under.box[0]) * CW;
const underPxH = (under.box[3] - under.box[1]) * CH;

// ---------- 判定 ----------
const near = (a: number, b: number, tol: number) => Math.abs(a - b) <= tol;
const lineEnd = line.box[2];
const rectBoxOk =
  near(rect.box[0], rectL, 0.014) &&
  near(rect.box[1], rectT, 0.014) &&
  near(rect.box[2], rectR, 0.014) &&
  near(rect.box[3], rectB, 0.014);
// 正方形：像素宽高差不该超过笔宽的两倍
const rectPxW = (rect.box[2] - rect.box[0]) * CW;
const rectPxH = (rect.box[3] - rect.box[1]) * CH;
const triBoxOk =
  near(tri.box[0], 0.32, 0.014) &&
  near(tri.box[1], 0.16, 0.014) &&
  near(tri.box[2], 0.68, 0.014) &&
  near(tri.box[3], 0.52, 0.014);
const circPxW = (circ.box[2] - circ.box[0]) * CW;
const circPxH = (circ.box[3] - circ.box[1]) * CH;
const circBoxOk =
  near(circPxW, 0.4 * U, 0.02 * U) && near(circPxH, 0.4 * U, 0.02 * U);

// pad 必须真的把圈推到字外面：至少吃掉一半的 26px，抖动吃不掉这么多
const padX = 26 / CW;
const padY = 26 / CH;
const padOk =
  !!textBox &&
  ringed.box[0] < textBox[0] - padX * 0.5 &&
  ringed.box[1] < textBox[1] - padY * 0.5 &&
  ringed.box[2] > textBox[0] + textBox[2] + padX * 0.5 &&
  ringed.box[3] > textBox[1] + textBox[3] + padY * 0.5;

const gapL = textBox ? (textBox[0] - ringed.box[0]) * CW : NaN;
const gapR = textBox ? (ringed.box[2] - (textBox[0] + textBox[2])) * CW : NaN;
const gapT = textBox ? (textBox[1] - ringed.box[1]) * CH : NaN;
const gapB = textBox ? (ringed.box[3] - (textBox[1] + textBox[3])) * CH : NaN;
// 上下要接近：框按墨迹量，不是按字号估（估会把 descender 的空算进来，圈整体下沉）
const balanced = Math.abs(gapT - gapB) <= 8 && Math.abs(gapL - gapR) <= 10;

console.log(`画布 ${CW}x${CH} 短边 ${U}`);
console.log("[线]  末端 x =", lineEnd.toFixed(4), "(理论 0.6)");
console.log("[矩形] box =", rect.box.map((v: number) => v.toFixed(3)).join(", "), "四角 =", rect.hits.join(""));
console.log(`[矩形] 像素宽高 = ${rectPxW.toFixed(1)} x ${rectPxH.toFixed(1)} (w===h 应为正方形)`);
console.log("[三角] box =", tri.box.map((v: number) => v.toFixed(3)).join(", "), "三角 =", tri.hits.join(""));
console.log(`[正圆] 像素宽高 = ${circPxW.toFixed(1)} x ${circPxH.toFixed(1)} (理论 ${(0.4 * U).toFixed(1)} 见方) 八向 = ${circ.hits.join("")}`);
console.log("[椭圆] 四向 =", ell.hits.join(""), `(左/右/上/下各一点)`);
if (textBox) {
  console.log(`[圈字] 字 box=${textBox.map((v) => v.toFixed(3)).join(", ")}`);
  console.log(`[圈字] 外扩 左${gapL.toFixed(1)} 右${gapR.toFixed(1)} 上${gapT.toFixed(1)} 下${gapB.toFixed(1)} (期望各约 26px)`);
} else {
  console.log("[圈字] 对象表里没有 h1 —— 这个阶段没跑成");
}
console.log(
  `[字上圆下] 圆顶离字底 ${gapUnder.toFixed(1)}px，水平偏移 ${centered.toFixed(1)}px，` +
    `圆的像素宽高 ${underPxW.toFixed(1)} x ${underPxH.toFixed(1)}`,
);

const checks = {
  "线段画到终点": lineEnd > 0.588,
  "矩形 bbox 完整": rectBoxOk,
  "矩形四角都有笔": rect.hits.every((h: number) => h === 1),
  "w===h 画出来是正方形": Math.abs(rectPxW - rectPxH) <= 8,
  "三角形 bbox 完整": triBoxOk,
  "三角形三顶点都有笔": tri.hits.every((h: number) => h === 1),
  "正圆像素宽高相等": Math.abs(circPxW - circPxH) <= 6,
  "正圆直径对得上": circBoxOk,
  "圆周覆盖 8/8": circ.hits.every((h: number) => h === 1),
  "椭圆四向都有笔": ell.hits.every((h: number) => h === 1),
  "ring pad 生效（字符串也认）": padOk,
  "ring 外扩上下均衡": balanced,
  "below 的圆在字下方（不压字）": gapUnder > 4,
  "below 的圆与字水平居中": centered <= 8,
  "below 的圆仍是正圆": Math.abs(underPxW - underPxH) <= 6,
  "无运行时异常": errors.length === 0,
};
for (const [k, v] of Object.entries(checks)) console.log(`${v ? "PASS" : "FAIL"}  ${k}`);
if (errors.length) console.log("[errors]", errors.slice(0, 3));

user.close();
ws.close();
proc.kill();
await sleep(300);
await Bun.$`rm -rf ${profile}`.quiet().catch(() => {});
process.exit(Object.values(checks).every(Boolean) ? 0 : 1);
