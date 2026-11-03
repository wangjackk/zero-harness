/**
 * 验证像素级行为：write 真的画了、erase 按对象擦干净、原子像素操作生效。
 *
 * 做法：用户侧先连 → 起无头浏览器 → 逐阶段推动作 → 每阶段读一次 canvas
 * 的不透明像素数。用数字判断，不靠肉眼（本机 Edge 截图功能被环境限制）。
 */
import { io } from "socket.io-client";
import { EV, type Action, type Obj, type Up } from "@bb/shared";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const CPORT = 9334;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ---------- 用户侧：先连，抢在页面之前，避免它触发任何演示 ----------
const user = io("http://localhost:3001", { transports: ["websocket"], auth: { role: "user" } });
await new Promise<void>((r) => user.on("connect", () => r()));
let objects: Obj[] = [];
/** 多块黑板同时在线（比如用户自己开着一个标签页）时，按来源分开存 */
const boards = new Map<string, Obj[]>();
let boardId = "";
user.on(EV.objects, (m: any) => {
  boards.set(m.from, m.objects);
  if (m.from === boardId) objects = m.objects;
});

const push = (act: Action, id: string) =>
  user.emit(EV.up, { type: "actions", items: [{ id, act }] } satisfies Up);

// ---------- 无头浏览器（每次新 profile，防止恢复旧标签页） ----------
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
      const p = Array.isArray(j) ? j.find((t: any) => t.type === "page" && String(t.url).includes("5173")) : null;
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

// 先认出"我这一块"黑板：页面连上时会把 socket.id 挂在 window 上
for (let i = 0; i < 40; i++) {
  const id = await evaluate(`window.__bbBoardId || ""`);
  if (id) {
    boardId = id;
    break;
  }
  await sleep(300);
}
if (!boardId) throw new Error("page never reported a board id");

const COUNT = `(() => {
  const c = document.querySelector('canvas');
  if (!c) return -1;
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let n = 0;
  for (let i = 3; i < d.length; i += 4) if (d[i] > 12) n++;
  return n;
})()`;
const count = async (): Promise<number> => await evaluate(COUNT);

/** 残留像素在哪：是矩形边缘（精度）还是别处（有东西没擦干净） */
const bbox = async () =>
  await evaluate(`(() => {
    const c = document.querySelector('canvas');
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let n = 0, x0 = 1e9, y0 = 1e9, x1 = -1, y1 = -1;
    for (let i = 3; i < d.length; i += 4) {
      if (d[i] <= 12) continue;
      n++;
      const p = (i - 3) / 4, x = p % c.width, y = (p / c.width) | 0;
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    return JSON.stringify({ n, x0, y0, x1, y1, w: c.width, h: c.height });
  })()`);

// ---------- 分阶段 ----------
await sleep(1800);
const c0 = await count();

push({ do: "write", text: "擦除测试", at: [0.2, 0.35], size: "l" }, "w1");
await sleep(2000);
const c1 = await count();
const objAfterWrite = objects.length;

push({ do: "erase", around: "w1" }, "e1");
await sleep(700);
const c2 = await count();
const objAfterErase = objects.length;

push({ do: "pixel", ops: [{ op: "fillRect", x: 0.1, y: 0.6, w: 0.3, h: 0.2, color: "#ff0000" }] }, "z1");
await sleep(500);
const c3 = await count();

push({ do: "pixel", ops: [{ op: "clearRect", x: 0.1, y: 0.6, w: 0.3, h: 0.2 }] }, "z2");
await sleep(500);
const c4 = await count();

// 重复擦同一个 id：必须幂等、不能报错
push({ do: "erase", around: "w1" }, "e2");
await sleep(300);
const c5 = await count();

console.log("[阶段] 空板        ", c0);
console.log("[阶段] 写完 w1     ", c1, `对象=${objAfterWrite}`);
console.log("[阶段] 擦掉 w1     ", c2, `对象=${objAfterErase}`);
console.log("[阶段] fillRect    ", c3);
console.log("[阶段] clearRect   ", c4);
console.log("[阶段] 重复擦除    ", c5);
console.log("[残留]", await bbox(), "(矩形应落在 0.1~0.4 / 0.6~0.8 之外才是问题)");
console.log("[其他黑板]", [...boards.keys()].filter((k) => k !== boardId).length, "块");

const checks = {
  "空板是空的": c0 === 0,
  "write 真的画了": c1 > 2000,
  "对象表登记了 w1": objAfterWrite === 1,
  "erase 擦掉了像素": c2 < c1 * 0.02,
  "erase 删掉了对象": objAfterErase === 0,
  "fillRect 增加像素": c3 > c2 + 2000,
  "clearRect 抹回原样": Math.abs(c4 - c2) < 200,
  "重复擦除幂等": Math.abs(c5 - c4) < 200,
  "无运行时异常": errors.length === 0,
};
for (const [k, v] of Object.entries(checks)) console.log(`${v ? "PASS" : "FAIL"}  ${k}`);
if (errors.length) console.log("[errors]", errors.slice(0, 3));

user.close();
ws.close();
proc.kill();
process.exit(Object.values(checks).every(Boolean) ? 0 : 1);
