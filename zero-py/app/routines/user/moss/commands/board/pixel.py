"""bb_pixel —— 直接改像素: 唯一的低层出口, body 是一段 ops.

语义动作画的是"东西", 这个画的是"像素": 不做动画、不登记对象表、到了就生效。
四个 op, 坐标同样是归一化 0~1(和语义动作一致, 黑板按当前画布换算):

    clearRect  x y w h                抹成透明(黑板擦)
    fillRect   x y w h [color] [alpha] 填一块色
    pixel      x y [color] [alpha]     一个像素
    image      x y w h src            贴图(data URL 或同源 http)

**为什么 ops 走 body 而不是属性**: ops 是嵌套记录, 一行就是一组"操作码 + 五个
字段", CTML 属性装不下数组, 硬塞就得自己发明一套行语法(第二套前端)。它本质上
是一小段程序, 和 ``code`` 的 body 是同一类东西 —— 属性放参数, body 放程序。

**为什么必须自己校验**: 黑板不校验 payload, 坏 op 静默跳过。这里 JSON 解析失败
或字段缺失都当场报错(带下标), 错误帧带注意债, 模型当拍就能改。
"""
from __future__ import annotations

import json
from typing import Annotated, Any, ClassVar, Dict, List, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from ...ctml import CtmlRoutine
from .base import BbCommand

__all__ = ['BbPixel']

#: ops 上限: 一条消息里几十个还说得过去, 上 hundreds 就是该用别的办法了
_MAX_OPS = 64


class _ClearRect(BaseModel):
    op: Literal['clearRect']
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class _FillRect(BaseModel):
    op: Literal['fillRect']
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)
    color: str = ''
    alpha: float = Field(default=1.0, ge=0, le=1)


class _Pixel(BaseModel):
    op: Literal['pixel']
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    color: str = ''
    alpha: float = Field(default=1.0, ge=0, le=1)


class _Image(BaseModel):
    op: Literal['image']
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)
    src: str = Field(min_length=1)


_PixelOp = Annotated[Union[_ClearRect, _FillRect, _Pixel, _Image],
                     Field(discriminator='op')]
_OneOp = TypeAdapter(_PixelOp)

_EXAMPLE = (
    'body 是**一个 JSON 数组**, 一个元素一个 op'
    '(顺序有意义: 先 clearRect 再 fillRect):\n'
    '[\n'
    '  {"op":"clearRect","x":0.62,"y":0.10,"w":0.30,"h":0.12},\n'
    '  {"op":"fillRect","x":0.62,"y":0.10,"w":0.30,"h":0.12,'
    '"color":"#f2efe2","alpha":0.08}\n'
    ']\n'
    '坐标是归一化 0~1(和 at / points 一样), 不是像素; '
    'alpha 是 0~1, color 不写就是粉笔白.\n'
    '它改的是位图本身: 擦掉的内容不会自己回来, 也没有撤销. '
    '像素不进对象表, 所以 ring / erase 指不到它们.'
)


class BbPixel(CtmlRoutine, BbCommand):
    """改像素: ops 数组, 无动画, 不进对象表."""

    name = 'bb_pixel'

    meta: ClassVar[Dict[str, Any]] = {
        **BbCommand.meta,
        # 收 body(默认是不收, 见 ctml.CtmlRoutine): ops 的 JSON 数组在标签中间.
        'body': True,
        'description': (
            '直接改像素(唯一低层出口): body = ops 的 JSON 数组, body 里每个 op '
            '是 clearRect / fillRect / pixel / image 之一. 没有动画、不进对象表; '
            '擦一块**区域**用它, 擦/圈某个**对象**用 bb_erase / bb_ring'),
        'example': _EXAMPLE,
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        await self._body_ready.wait()
        raw = self.body_text.strip()
        if not raw:
            raise RuntimeError(
                'bb_pixel 的 body 是空的: 要改的像素操作放在标签中间(JSON 数组)')
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f'body 不是合法 JSON: {exc.msg}(第 {exc.lineno} 行第 '
                f'{exc.colno} 列)—— 要一个数组, 例如 '
                '[{"op":"clearRect","x":0.6,"y":0.1,"w":0.3,"h":0.1}]') from None
        if isinstance(data, dict) and 'ops' in data:
            raise RuntimeError(
                'body 直接写数组, 不要再包一层 {"ops": ...}')
        if not isinstance(data, list):
            raise RuntimeError(
                f'body 要一个 JSON 数组(一个元素一个 op): {type(data).__name__}')
        if not data:
            raise RuntimeError('ops 是空的: 至少一个操作')
        if len(data) > _MAX_OPS:
            raise RuntimeError(
                f'ops 有 {len(data)} 个(上限 {_MAX_OPS}): 拆成几次 bb_pixel')
        ops: List[Dict[str, Any]] = []
        for i, item in enumerate(data):
            try:
                ops.append(_OneOp.validate_python(item).model_dump(
                    exclude_defaults=True))
            except ValidationError as exc:
                err = exc.errors()[0]
                where = '.'.join(str(x) for x in err['loc']) or 'op'
                raise RuntimeError(
                    f'ops[{i}] 不合法({where}): {err["msg"]} —— 合法 op 只有 '
                    'clearRect / fillRect / pixel / image, 坐标归一化 0~1'
                ) from None
        r = await self._draw({'do': 'pixel', 'ops': ops})
        return {'content': f'画了 {len(ops)} 个像素操作',
                'details': {'ops': len(ops), 'id': r.get('id')}}
