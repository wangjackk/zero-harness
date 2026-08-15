---
name: routine_bridge
agents: [prime]
description: "IPython kernel 内置的 run_routine 函数,用于从 kernel 内调用任意 routine (read/edit/grep/list_routines 等)。需要文件操作、搜索、shell 命令时使用本 skill。"
---

# routine_bridge: kernel 内调用 routine

IPython kernel 启动时自动注入了 `run_routine` 异步函数,可直接在 cell 里调用 routine。

## 调用方式

```python
result = await run_routine('<routine_name>', kwargs={<参数 dict>})
```

- 第一个参数: routine 名称
- `kwargs`: routine 的输入参数,用 dict 包裹 (避免跟 routine 自身的 name 等参数冲突)
- 无参数时 `kwargs` 可省略

`run_routine` 自动提取 `for_llm` 字段 (LLM-friendly 文本摘要),适合 kernel agent 直接处理。

## 发现可用 routine

```python
routines = await run_routine('list_routines')
print(routines)  # 文本摘要: "91 routines, 2 hubs:\n  zero(78, 5p): ..."
```

查看运行中的实例:

```python
running = await run_routine('list_routines', kwargs={'kind': 'running'})
print(running)
```

## 查看 routine 文档

不确定参数时,用 `routine_doc` 查看精简文档:

```python
doc = await run_routine('routine_doc', kwargs={'name': 'echo'})
print(doc)
```

## 常用 routine 示例

```python
# 文件操作
content = await run_routine('read', kwargs={'path': 'foo.py'})
await run_routine('write', kwargs={'path': 'bar.py', 'content': '...'})

# 搜索
matches = await run_routine('grep', kwargs={'pattern': 'TODO', 'path': 'src/'})

# shell 命令
output = await run_routine('bash', kwargs={'command': 'git status'})
```

## 注意

- `run_routine` 是 async 函数,必须 `await`
- 走 HTTP bridge (默认 127.0.0.1:7780),需要 WebServer routine 在运行
- bridge 地址可通过 `ZERO_HTTP_ADDR` 环境变量配置
