"""routine/doc —— 取回一个 routine 的完整用法(签名 / 参数说明 / 示例).

能力清单分两级披露: 装配 ``detail:`` 里点名的详列, 其余只印名字. 名字在
眼前、用法留在门外 —— 真要用时用这一句取回来:

    <routine/doc name="car/move"/>

**数据源是 routine 自己的 meta**, 渲染走 ``prompt/capability.entry`` —— 与
清单里那一项是**同一个函数**. 两处各写一遍格式就会漂移: 清单印
``speed=0.1``, 查回来又是另一种写法, 模型还得自己翻译一遍 (翻译就是猜).

只查 catalog(kernel 路由表), 不经过 shell: 这具身体与别的装配共用这份
routine, 不该为一个"查文档"的动作依赖于某具身体里有谁在跑. 名字在不在
词表里由投递口管 —— 文档说清楚"清单里有这些名字"就够.

**也不声明 ``observe``**: 查回来的结果该不该进下一拍, 是调用它的那具
身体的判断(见 moss 装配清单的 ``observe:`` 名单), 不是这个 routine 的
属性. 跨身体共用的单元把某具身体的调用语法写进 meta, 换个装配就变成
一句没人读的谎.
"""
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from app.routines.user.moss.prompt.capability import canon, entry
from routine import Routine

__all__ = ['RoutineDoc', 'RoutineDocInput']


class RoutineDocInput(BaseModel):
    """routine/doc 输入: 必须指定 name."""

    name: str = Field(
        description='要查的 routine 名, 照清单里那一行原样写(如 car/move, bb_write)',
    )


class RoutineDoc(Routine):
    """取回一个 routine 的完整用法(签名 + 参数说明 + 示例)."""

    name = 'routine/doc'

    meta: ClassVar[Dict[str, Any]] = {
        'description': ('取回一个 routine 的完整用法(签名/参数说明/示例): '
                        '清单里只列了名字的那些, 用前先查它'),
        'input_schema': RoutineDocInput.model_json_schema(),
        'example': (
            '要用清单里只列了名字的能力, 先取它的参数:\n'
            '<routine/doc name="car/move"/>'),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = RoutineDocInput.model_validate(kwargs or {})
        name = canon(inp.name)
        metas = {e.get('name'): e.get('meta') or {}
                 for e in await self.get_routines()}
        meta = metas.get(name)
        if meta is None:
            # 名字写错与"装配漏了这个文件"对模型是同一件事: 它两个都改不了,
            # 能做的是换个名字. 所以一句话说清"没有这个名字", 别说 catalog.
            raise RuntimeError(
                f'没有这个 routine: {inp.name}(照清单里的名字写, 别自己起名)')
        self._logger.info('routine/doc: %s', name)
        return {'content': entry(name, meta)}
