import { createServer } from "node:http";
import { Server } from "socket.io";
import { EV, type Down, type Obj, type ObjSnapshot, type Role, type Up } from "@bb/shared";
import { DEMO, type Batch } from "./script";

const PORT = Number(process.env.PORT ?? 3001);
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

const http = createServer((req, res) => {
  if (req.url === "/health") {
    res.writeHead(200, { "content-type": "text/plain" });
    res.end("ok");
    return;
  }
  res.writeHead(404);
  res.end();
});

const io = new Server(http, { cors: { origin: "*" } });

/**
 * 一块黑板 = 一条动作流，所以 seq 是**服务端全局**的，不是每连接一份。
 * 谁产的都一样：用户侧推上来的动作和本地演示流抢同一个计数器。
 */
let seq = 0;
/** 播放循环的代际号。换一代，旧循环自己退出 */
let token = 0;

/** 黑板侧：收 bb:down */
const viewers = new Set<{ emit: (e: string, m: unknown) => void }>();
/** 用户侧：收 bb:objects，发 bb:up{type:'actions'} */
const users = new Set<{ emit: (e: string, m: unknown) => void }>();

function broadcast(items: { act: unknown; id: string }[], sync?: { start: number; end: number }) {
  if (users.size > 0) return; // 有用户侧接管时，演示流不许抢镜
  seq += 1;
  const msg: Down = { type: "actions", seq, items: items as never, sync };
  for (const v of viewers) v.emit(EV.down, msg);
}

/**
 * 演示流默认**关**。黑板是静态的：没人说话就是空板，不该自己画画。
 * 想看效果：`BB_DEMO=1 bun run dev`。
 */
const DEMO_ON = process.env.BB_DEMO === "1";

/** 本地演示脚本，只在显式开启时跑，且有用户侧接管时让位 */
async function play(force = false) {
  if (!DEMO_ON) return;
  const my = ++token;
  for (const batch of DEMO) {
    if (token !== my) return;
    if (users.size > 0 && !force) return;
    broadcast(batch as Batch);
    await sleep(520 + Math.random() * 420);
  }
}

io.on("connection", (socket) => {
  const role: Role = socket.handshake.auth?.role === "user" ? "user" : "viewer";

  if (role === "user") {
    users.add(socket);
    // 用户侧连上即接管：掐掉正在播的演示
    token += 1;
  } else {
    viewers.add(socket);
    if (DEMO_ON && users.size === 0) setTimeout(() => void play(), 700);
  }
  console.log(
    "[io] connect",
    socket.id,
    role,
    `viewers=${viewers.size} users=${users.size}`,
  );

  socket.on(EV.up, (raw: Up) => {
    switch (raw?.type) {
      case "actions": {
        if (role !== "user") break; // 只认用户侧下发的动作，黑板侧不能自己造
        seq += 1;
        const msg: Down = { type: "actions", seq, items: raw.items, sync: raw.sync };
        for (const v of viewers) v.emit(EV.down, msg);
        break;
      }

      case "input":
        // 黑板侧的输入（演示路径）：先掐掉正在讲的那段，再重讲
        token += 1;
        for (const v of viewers) v.emit(EV.down, { type: "abort", seq } satisfies Down);
        void play();
        break;

      case "abort":
        token += 1;
        for (const v of viewers) v.emit(EV.down, { type: "abort", seq } satisfies Down);
        break;

      case "objects": {
        // 对象表回灌：带上来源，多块黑板在线时用户侧能分辨是谁报的
        const msg: ObjSnapshot = {
          type: "objects",
          from: socket.id,
          seq: raw.seq,
          objects: raw.objects as Obj[],
        };
        for (const u of users) u.emit(EV.objects, msg);
        break;
      }
    }
  });

  socket.on("disconnect", () => {
    viewers.delete(socket);
    users.delete(socket);
    console.log("[io] disconnect", socket.id, `viewers=${viewers.size} users=${users.size}`);
  });
});

http.listen(PORT, () => console.log(`[server] ws://localhost:${PORT}`));
