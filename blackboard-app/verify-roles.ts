import { io } from "socket.io-client";
import { EV, type Down, type Up } from "@bb/shared";

const URL = "http://localhost:3001";
const got: string[] = [];
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

(async () => {
  // 先连用户侧，再连黑板侧：黑板侧不该收到任何演示动作
  const user = io(URL, { transports: ["websocket"], auth: { role: "user" } });
  await new Promise<void>((r) => user.on("connect", () => r()));

  const viewer = io(URL, { transports: ["websocket"], reconnection: false });
  viewer.on(EV.down, (m: Down) => {
    if (m.type === "actions") got.push(`viewer<-${m.seq}:` + m.items.map((i) => i.id).join(","));
    else got.push("viewer<-" + m.type);
  });
  user.on(EV.objects, (m: any) => got.push(`user<-objects#${m.seq}:${m.objects.length}`));
  await new Promise<void>((r) => viewer.on("connect", () => r()));

  await wait(1500);
  const demoLeak = got.length;

  viewer.emit(EV.up, { type: "actions", items: [{ id: "x1", act: { do: "write", text: "非法", at: [0.1, 0.1] } }] } satisfies Up);
  await wait(150);
  user.emit(EV.up, { type: "actions", items: [{ id: "a1", act: { do: "write", text: "斜率", at: [0.12, 0.28] } }] } satisfies Up);
  await wait(200);
  viewer.emit(EV.up, { type: "objects", seq: 1, objects: [{ id: "a1", kind: "text", text: "斜率", box: [0.1, 0.2, 0.1, 0.1] }] } satisfies Up);
  await wait(150);
  user.emit(EV.up, { type: "abort" } satisfies Up);
  await wait(300);

  console.log("--- 收到 ---");
  console.log(got.join("\n") || "(空)");
  const checks = {
    "用户侧在时不播演示": demoLeak === 0,
    "用户侧的动作到黑板侧": got.some((g) => new RegExp("viewer<-\\d+:a1").test(g)),
    "黑板侧造的动作被丢": !got.some((g) => g.includes("x1")),
    "对象表转给用户侧": got.some((g) => g.includes("user<-objects#1:1")),
    "abort 到黑板侧": got.some((g) => g.includes("viewer<-abort")),
  };
  for (const [k, v] of Object.entries(checks)) console.log(`${v ? "PASS" : "FAIL"}  ${k}`);
  user.close(); viewer.close();
  process.exit(Object.values(checks).every(Boolean) ? 0 : 1);
})();
