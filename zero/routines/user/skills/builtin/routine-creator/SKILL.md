---
name: routine-creator
agents: [prime]
description: "给系统写新 routine 并热加载自验：写文件到 dynamic 实验场、manifest re-export、watcher 自动热注册、真实调用自验。需要新增可执行能力（工具/服务/编排）时使用；纯知识用 skill-creator。"
---

# routine-creator: 写 routine

你运行在 zero 上：**万物皆 routine**——工具是 routine，agent 是 routine，编排器是 routine，没有特权公民。每个 routine 经 kernel 路由表对外可见，watcher 让你写的 routine 保存即生效、无需重启。

代码级扩展是 **routine**（可执行、有 schema、走 kernel 路由），不是 skill（skill 是静态知识）。
用户要的"新能力"若需要执行逻辑，写 routine。

写 routine 的 SDK API（生命周期/ctx/handle/异常）查 `routine-sdk` skill，本 skill 只讲创作闭环。
文中的"调 `<name>`"指调用对应 routine；kernel 内的调用机制见 `routine_bridge` skill。

## 禁区

- 实验期**只在** dynamic/ 写，不直接改正式包和 `routines.yaml`——转正是实验稳定后的事（见文末）
- 永不编辑 `routines/watcher.py`、`routines/loader.py` 框架本身；坏掉会禁用热重载这个能力本身

## 落点

实验性 routine 一律放 dynamic 实验场（watcher 热重载，改完即生效）：

```
zero/routines/user/dynamic/       <- 实验场（相对项目根）
  dynamic_demo.py                 <- 活样例，照这个形状写
  __init__.py                     <- manifest: __all__ re-export
```

## 工作流

### 1. 写 routine 文件

新建 `zero/routines/user/dynamic/<name>.py`，最小形状：

```python
from typing import Any, ClassVar, Dict
from pydantic import BaseModel, Field
from routine import Routine

class MyThingInput(BaseModel):
    text: str = Field(description='要处理的文本')

class MyThing(Routine):
    name = 'my_thing'                # 调用名, snake_case
    meta: ClassVar[Dict[str, Any]] = {
        'description': '一句话用途',
        'input_schema': MyThingInput.model_json_schema(),   # LLM 可见的入参 schema
    }

    async def run(self, kwargs: Dict[str, Any]):
        inp = MyThingInput.model_validate(kwargs)
        return {'result': inp.text.upper()}
```

schema 一律用 pydantic BaseModel 声明（`Field(description=...)` 写注释），`model_json_schema()` 生成进 meta——类型安全、IDE 补全，不手写 JSON dict。`run` 入口先 `model_validate(kwargs)`，入参错直接抛 ValidationError，不脏数据。

文件名规则：不要 `_` 前缀、不要 `test_` 前缀（watcher 跳过不监控）。
`name` 起名前先查重：调 `check_routine_name`（`name`='my_thing'）——热重载同名覆盖，撞名会劫持已有 routine 的路由。

### 2. manifest re-export

编辑 `dynamic/__init__.py`：

```python
from zero.routines.user.dynamic.my_thing import MyThing

__all__ = ['DynamicDemo', 'MyThing']
```

文件和 manifest 谁先写都行——顺序无关，watcher 自动收敛（见热重载契约）。

### 3. 确认热注册

保存后等约 2s（1s 轮询 + 0.5s 防抖），然后确认：

- 调 `list_routines` → 应出现新 routine 名
- 调 `routine_doc`（`name`='my_thing'）→ 确认 schema/description 正确

没出现就再等 1-2s；还没有说明文件有错，看步骤 5。

### 4. 自验（必做）

调一次新 routine（如 `my_thing`，`text`='hello'）。真实调用走 kernel 路由，一次验证注册 + schema + 路由。不交付没跑通的 routine。

### 5. 迭代

直接改 `<name>.py` 保存，watcher 自动 reload（同名覆盖），重复步骤 4。

热重载契约：
- **upsert 语义**：新名字直接注册，无需先 register；同名覆盖老版本
- **顺序无关**：文件/manifest 谁先写都行。import 失败（写到一半、manifest 先于文件、语法错）时 watcher 每秒自动重试，两边写齐后 ~1.5s 内自动注册
- **中间态安全**：失败期间老版本继续服务（kernel 路由不动）；修好保存后下一轮自动收敛
- 卸载 = 从 `__all__` 撤下（自动 deregister），文件随后删不删都行

## 约束（硬规则）

- schema 用 **pydantic BaseModel** 声明，`model_json_schema()` 生成 `input_schema`/`output_schema`；禁止手写 JSON dict
- 模块级**无状态**：状态放实例字段，不放模块级变量
- routine 间通信走 wire protocol（`self.req` / `ctx.req`），禁止全局变量传状态
- name / schema 以类声明为唯一事实源，不在别处重复声明
- 需要独占资源（音频/串口等）时 `on_created` 返回 `Modules([...])` 声明占用

前两条不是风格偏好，是硬性架构约束（watcher 会 `importlib.reload` 模块，模块级状态每次 reload 都会重置丢失；热重载会替换类，全局变量引用的旧类实例与 kernel 路由的新类脱节）。

正确：

```python
class Counter(Routine):
    def __init__(self):
        super().__init__()
        self._count = 0          # 状态在实例上, reload 后新实例自然重建

    async def run(self, kwargs):
        self._count += 1
        other = await self.req(target_rid, 'query', {})   # 通信走 wire protocol
        return {'count': self._count}
```

错误：

```python
_count = 0                        # 模块级状态: reload 即重置, 多实例共享错乱

class Counter(Routine):
    async def run(self, kwargs):
        global _count
        _count += 1
        set_shared_state(_count)  # 全局变量传状态: 绕过 kernel, 对端不可见
        return {'count': _count}
```

## 失败排查

| 现象 | 先查 |
|---|---|
| list_routines 里没有新名字 | 等几秒再查（轮询+防抖，失败自动重试）；确认 `__all__` 里有该类；文件名是否 `_`/`test_` 前缀（不监控） |
| 等了 5s+ 仍没有 | 文件有错（语法/ImportError）：watcher 正在每秒重试，修好保存后下一轮自动注册，无需手动触发 |
| routine_doc 显示的 schema 是旧的 | 同名覆盖生效前的缓存；重新调一次即新 |
| 调用报 routine not found | name 字段与调用名不一致（调用名是 `name` 属性，不是类名/文件名） |
| 调用报参数错误 | `meta.parameters` schema 与 run() 实际取的 kwargs 字段对不上，对照修 schema |
| 想知道某名字是否已被占用 | 调 `check_routine_name`（`name`=...）；**热重载是同名覆盖——dynamic 里起了与正式 routine 相同的 name 会劫持它的路由**，起名前先查 |

## 转正

实验稳定后，从 dynamic/ 移到正式位置（如 `zero/routines/user/`）：
1. 从 `dynamic/__init__.py` 的 `__all__` 撤下（watcher 自动 deregister dynamic 路由）
2. 移文件 + 在目标包 `__init__.py` re-export（或单文件直接挂 yaml）
3. `routines.yaml` 加条目（watcher 检测 yaml 变更自动 diff 注册）
4. 再真实调一轮自验

中间态顺序随意，watcher 自动收敛；manifest 最终保持干净即可（残留已删文件的 import 会让该包 reload 持续失败重试）。

## 分工

| 要加什么 | 用哪个 |
|---|---|
| 可执行能力（工具/服务/编排） | 本 skill（写 routine） |
| 静态知识/流程指导（SKILL.md） | `skill-creator` |
| 组装既有能力成新 agent | `editing-agent-presets` |
