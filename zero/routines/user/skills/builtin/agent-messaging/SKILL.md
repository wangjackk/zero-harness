---
name: agent-messaging
description: "agent 间消息通信规范。当收到的消息以 from 头开头时，这是来自其他 agent 的消息，按本规范处理和回复。涵盖 send_message routine 的使用方式。"
---

# Agent 间消息通信规范

## 识别来自其他 agent 的消息

收到的消息若首行为 `from: <agent_id>`，表示这条消息来自另一个 agent/消息通知：

```
from: prime_1
<消息正文>
```

- `from` 后的 id 是发送方 agent 的标识（如 `prime_1` / `prime_2`）
- 换行后是消息正文

## 如何回复

回复其他 agent 时调用 `send_message` routine（单向异步）：

- `to`: 目标 agent 的 id（即 `from` 头里的 id）
- `message`: 消息正文

`send_message` 发送后立即返回成功/失败，**不等待对方处理或回复**。对方收到后会自己决定是否调 `send_message` 进行回复。

## 行为规范

- 收到 agent 消息时,如有必要可以通过 `send_message` 发回给发送方。
- agent 消息是独立的对话发起方，不要当作人类用户指令的延续。
- 回复保持简洁，聚焦对方请求的内容。
- 若消息正文与你的职责无关，可礼貌说明并简短回复。
