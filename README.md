# zero-harness

![架构总览](docs/architecture.svg)

## 设计哲学

**万物皆 routine**——agent 是 routine，工具是 routine，Web server 是 routine，连 loader
和 watcher 也是 routine。任何 routine 用任意语言编写。
每个 hub 独立进程、任意语言、任意设备.

## 快速开始（Windows）

前置：[Go](https://golang.google.cn/dl/)、[uv](https://docs.astral.sh/uv/getting-started/installation/)、bun 或 node（任一）。

1. 复制 `zero/models.yaml.example` 为 `zero/models.yaml`，填入你的 api_key。
2. 双击根目录 `start.bat`。

浏览器打开 <http://localhost:5173>。

## 更多

- [routine-py/docs/](routine-py/docs/) — 框架 API 与概念详解
- [zero/docs/](zero/docs/) — zero 项目约定、模块清单、端到端案例
- [routine-rs/examples/](routine-rs/examples/) — Rust SDK 示例
