# Routine creator：给 Moss 长出新能力

用户要的是一个需要执行逻辑的新能力时，在 `dynamic/` 里写一个 routine。不要把静态知识写成 routine，也不要为了创作能力改正式包、`moss.yaml`、`watcher.py` 或 `loader.py`。

你能用 `code` 写文件、调运维 routine 并做真实自验。完整闭环是：**写源码 → 改 manifest → 等 watcher 注册 → 挂进当前词表 → 真实调用自验**。只做前两步不算完成。

## 落点与最小形状

实验场目录从已加载的包定位，不猜机器上的绝对路径：

```python
from pathlib import Path
from app.routines.user.moss import dynamic
root = Path(dynamic.__file__).resolve().parent
```

在 `root / '<name>.py'` 新建文件。文件名不要以 `_` 或 `test_` 开头。最小形状：

```python
from typing import Any, ClassVar, Dict
from pydantic import BaseModel, Field
from routine import Routine

class MyThingInput(BaseModel):
    text: str = Field(description='要处理的文本')

class MyThing(Routine):
    name = 'my_thing'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '一句话用途',
        'input_schema': MyThingInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]):
        inp = MyThingInput.model_validate(kwargs)
        return {'content': inp.text.upper()}

    async def stop(self) -> None:
        pass
```

新名字先查重，避免同名覆盖劫持已有路由：

```python
check = await call('routine/check_name', name='my_thing')
print(check)
```

schema 用 pydantic `BaseModel` 与 `Field(description=...)` 声明；状态放实例字段；routine 间通信用 `self.call` / `self.req` / `self.publish`，不要用模块级变量传状态。需要收 body 时继承 `app.routines.user.moss.ctml.CtmlRoutine`，在自己的 `meta` 里显式写 `'body': True`。

## 让结果被看见：observe

`observe` 表示“这个结果需要驱动 Moss 再看一眼”，不是等待、同步返回值或 routine 的输入参数。不要把它写进 `Input` schema。三种用法按范围选择：

- **只这一次要结果**：调用标签写 `<my_thing text="hello" observe="true"/>`。它结束后再起一拍，把成功或失败回执放进 `<observe>`；只影响这次调用。下一步依赖结果时才声明，不要把每次调用都标上。
- **这个 Moss 专用 routine 每次都要被看**：例如查询、感知类能力，在类自己的 `meta` 中加 `'observe': True`。修改 `meta` 后必须重新 `shell/registry reg`，当前 Shell 才会重扫声明。能被其他身体复用的通用 routine 不写这个 Moss 编排声明，应由各身体的装配配置决定。
- **只有某次结果值得立即注意**：例如超时或循环结束，在该次 `run` 的返回值中加 `'observe': True`：`return {'content': '已超时', 'observe': True}`。普通成功结果不必加。

回执只认 `content`、`details`、`images`、`observe`；给模型看的结论写 `content`，结构化排障数据写 `details`，其余结果键会被拒绝。`code` 里的 `await call(...)` 已经直接拿到返回值，不需要也不能用 CTML 的 `observe="true"` 代替 `await`。

注意：`call()` 在代码里返回原始回执，所以代码能读 `details`；“details 不直接进模型上下文”只指 CTML 回执渲染。**不要 `print()` 整个返回字典**：print 会把它变成 stdout，而 `code` 会把 stdout 写进自己的 `content`，等于主动把 details 灌回模型。只打印 `content` 或确实需要检查的某个字段。

## 写 manifest

读 `root / '__init__.py'`，保留已有导出，加入：

```python
from .my_thing import MyThing

__all__ = ['DynamicProbe', 'MyThing']
```

源码与 manifest 谁先保存都可以。watcher 每 1s 轮询、0.5s 防抖；语法错误或 import 中间态会保留旧版本并自动重试，写齐后通常约 1.5s 收敛。

## 等注册并挂进当前词表

注册和挂载是两件事：注册让 kernel 能按名路由；挂载让当前 Moss 的 CTML 词表认识它。轮询确认注册后，再挂载：

```python
name = 'my_thing'
for _ in range(20):
    listed = await call('routine/list')
    if any(r.get('name') == name for r in listed.get('routines', [])):
        break
    await asyncio.sleep(0.5)
else:
    raise RuntimeError(f'{name} 尚未注册；先修源码或 manifest')

mounted = await call('shell/registry', op='reg', routine=name)
print('mount:', mounted.get('content'))
```

每次修改 routine 的 `meta` 或 schema 后，也重新执行一次 `reg`，让当前词表重扫声明。能力清单的系统前缀是装配时快照，不会自动重写；但挂载后立即可用 `call(name, ...)` 或对应 CTML 标签调用，用法也可由 `routine/doc` 查询。

## 必做：真实自验

用符合 schema 的真实参数调用一次，不要只看注册表就宣布完成：

```python
result = await call('my_thing', text='hello')
print('self-check:', result.get('content'))
# 若必须检查 details，只取目标字段，不打印整个 result。
```

失败就修改同名源码，等 watcher 重载后再挂载、再调用。同名修改是覆盖语义；从 manifest 撤销导出或删除源文件会注销路由。

如果新能力使用了 `observe`，还要用 CTML 标签实际调用一次，确认结果通过 `<observe>` 回灌并触发下一拍；只用 `code` 的 `await call` 自验，验证不到这条回灌链路。未声明出厂 `observe` 时，自验标签写 `<my_thing text="hello" observe="true"/>`；已声明时省略该属性，借这次调用同时验证出厂声明已生效。

卸载时同时从当前词表摘掉：

```python
await call('shell/registry', op='unreg', routine='my_thing')
```

怀疑 watcher 没收敛时，强制重载这个清单条目：

```python
print(await call('routine/reload', path='routines/user/moss/dynamic'))
```

最后向用户如实报告：routine 名、源码位置、是否已挂载、实际自验结果；失败就给出错误，不把“文件已保存”说成“能力已可用”。
