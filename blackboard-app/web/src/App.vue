<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { io, type Socket } from "socket.io-client";
import { EV, type Down, type Up } from "@bb/shared";
import { Board } from "./board/board";

const SERVER = "http://localhost:3001";

const canvasEl = ref<HTMLCanvasElement>();
const status = ref<"idle" | "on" | "off">("idle");
const objCount = ref(0);

let board: Board | null = null;
let socket: Socket | null = null;
/** 对象表回灌：bbox 只有黑板侧量得准，用户侧靠它知道上一句写在哪 */
let objTimer = 0;
let objSeq = 0;

function pushObjects(seq: number) {
  if (!board || !socket) return;
  objSeq = seq;
  window.clearTimeout(objTimer);
  objTimer = window.setTimeout(() => {
    const objects = board!.objects.list();
    objCount.value = objects.length;
    socket?.emit(EV.up, { type: "objects", seq: objSeq, objects } satisfies Up);
  }, 220);
}

function clearBoard() {
  board?.clear();
  objCount.value = 0;
}

function onResize() {
  board?.resize();
}

onMounted(() => {
  const el = canvasEl.value;
  if (!el) return;

  board = new Board(el);
  board.start();

  socket = io(SERVER, { transports: ["websocket"], reconnectionDelayMax: 3000 });
  socket.on("connect", () => {
    status.value = "on";
    // 多块黑板同时在线时，用户侧靠这个 id 认出是哪一块报了对象表
    (window as unknown as { __bbBoardId?: string }).__bbBoardId = socket?.id;
  });
  socket.on("disconnect", () => (status.value = "off"));
  socket.on("connect_error", () => (status.value = "off"));
  socket.on(EV.down, (msg: Down) => {
    board?.ingest(msg);
    if (msg.type === "actions") pushObjects(msg.seq);
    if (msg.type === "resync") pushObjects(msg.seq);
  });

  window.addEventListener("resize", onResize);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", onResize);
  socket?.close();
});
</script>

<template>
  <div class="wrap">
    <canvas ref="canvasEl" class="stage" />
    <div class="hud">
      <span class="dot" :class="status" />
      <span class="time">{{ objCount }} 个对象</span>
      <button @click="clearBoard">清屏</button>
    </div>
  </div>
</template>

<style>
.wrap {
  position: relative;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: #16241d;
}
.wrap::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(
    ellipse at 50% 42%,
    rgba(255, 255, 255, 0.045) 0%,
    rgba(0, 0, 0, 0) 45%,
    rgba(0, 0, 0, 0.55) 100%
  );
}
.stage {
  display: block;
  width: 100%;
  height: 100%;
}
.hud {
  position: absolute;
  left: 50%;
  bottom: 20px;
  transform: translateX(-50%);
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px 14px;
  border-radius: 10px;
  background: rgba(10, 16, 13, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(8px);
  font: 13px/1 "Segoe UI", system-ui, sans-serif;
  color: #cfd6cf;
  z-index: 5;
  opacity: 0.5;
  transition: opacity 0.2s ease;
}
.hud:hover {
  opacity: 1;
}
.hud button {
  font: inherit;
  color: #cfd6cf;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 6px;
  padding: 6px 11px;
  cursor: pointer;
}
.hud button.on {
  background: rgba(159, 225, 203, 0.22);
  border-color: rgba(93, 202, 165, 0.5);
  color: #b8f0dd;
}
.time {
  min-width: 44px;
  color: #8d998f;
  font-variant-numeric: tabular-nums;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #6b736c;
}
.dot.on {
  background: #5dcaa5;
  box-shadow: 0 0 8px rgba(93, 202, 165, 0.7);
}
.dot.off {
  background: #e24b4a;
}
</style>
