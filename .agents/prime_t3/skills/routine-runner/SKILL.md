---
name: routine-runner
description: "通过 run_routine 调用系统中的 routine。需要查可用 routine、调用某个 routine 时使用本 skill。"
---

# Routine 调用指南

你可以通过 `run_routine` 调用系统中的所有 routine。

## 发现可用 routine

调用 `list_routines` 列出已注册的 routine:

```json
{"name": "list_routines", "kwargs": {}}
```

查看运行中的实例:

```json
{"name": "list_routines", "kwargs": {"kind": "running"}}
```

## 调用 routine

用 `run_routine` 调用任意已注册 routine:

```json
{"name": "<routine_name>", "kwargs": {"<参数名>": "<值>"}}
```

- `name`: routine 名称
- `kwargs`: routine 的输入参数（无参数时不填）

返回值是 routine 的输出。

## 查看 routine 文档

不确定某个 routine 的参数时，用 `routine_doc` 查看精简文档（签名 + 参数列表）:

```json
{"name": "routine_doc", "kwargs": {"name": "<routine_name>"}}
```
