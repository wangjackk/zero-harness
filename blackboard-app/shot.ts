/**
 * 把几个关键效果画到画布上，再导出 PNG。
 * 用途：改完几何/布局后不用开浏览器也能看到实际画面。
 */
import { io } from "socket.io-client";
import { EV, type Action, type Up } from "@bb/shared";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const CPORT = 9335;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const user = io("http://localhost:3001", { transports: ["websocket"], auth: { role: "user" } });
await new Promise<void>((r) => user.on("connect", () => r()));
const push = (act: Action, id: string) =>
  user.emit(EV.up, { type: "actions", items: [{ id, act }] } satisfies Up);

const profile = `D:/kshell/zero-harness/blackboard-app/.edge-shot-${Date.now()}`;
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
    "--window-size=1200,760",
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
ws.onmessage = (ev) => {
  const m = JSON.parse(String(ev.data));
  if (m.id && pending.has(m.id)) {
    pending.get(m.id)!(m);
    pending.delete(m.id);
  }
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
for (let i = 0; i < 40; i++) {
  if (await evaluate(`window.__bbBoardId || ""`)) break;
  await sleep(300);
}
await sleep(1200);

// 清屏后画一屏对照
push({ do: "erase", around: "all" }, "z0");
await sleep(500);

push({ do: "write", text: "圆形", at: [0.10, 0.22], size: "l" }, "h1");
await sleep(900);
push({ do: "circle", at: "below:h1", r: 0.09 }, "c1");
await sleep(1800);

push({ do: "write", text: "斜率就是变化率", at: [0.42, 0.22], size: "m" }, "h2");
await sleep(1400);
push({ do: "ring", around: "h2", pad: 18 }, "r1");
await sleep(1600);

push({ do: "rect", at: [0.52, 0.72], w: 0.18, h: 0.18 }, "s1");
push({ do: "circle", at: [0.78, 0.72], r: 0.09 }, "c2");
await sleep(2400);

const dataUrl: string = await evaluate(`(() => {
  const c = document.querySelector('canvas');
  const t = document.createElement('canvas');
  t.width = c.width; t.height = c.height;
  const g = t.getContext('2d');
  g.fillStyle = '#0e1713';
  g.fillRect(0, 0, t.width, t.height);
  g.drawImage(c, 0, 0);
  return t.toDataURL('image/png');
})()`);

const b64 = String(dataUrl).split(",")[1] ?? "";
const out = "D:/kshell/zero-harness/blackboard-app/shot.png";
await Bun.write(out, Buffer.from(b64, "base64"));
console.log("saved", out, Math.round(b64.length / 1024), "KB");

user.close();
ws.close();
proc.kill();
await sleep(300);
await Bun.$`rm -rf ${profile}`.quiet().catch(() => {});
process.exit(0);
