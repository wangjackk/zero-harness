"""bb_write —— 在黑板写一行字: body 是这一行的原文.

body 是**内容**, 不是参数: 一句话里会有引号、中文标点、公式符号, 塞进属性
要转义, 写坏黑板也不吭声(它不校验 payload). 走 body 就和 ``<say>`` 一个
形状, 模型不用学第二种写法.

收完才落笔(默认 CtmlRoutine 行为): 黑板侧本来就是整段一次性开写、逐字扫出,
流式喂没有意义.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from ...ctml import CtmlRoutine
from .base import BbCommand, anchor, parse, obj_id, size_of

__all__ = ['BbWrite']

#: 一行最多多少字 —— 超过画布宽度会被钳在 0.99, 剩下的字挤出画面
_MAX_CHARS = 120


class BbWriteInput(BaseModel):
    """写一行: 位置/字号是参数, 文字本身走 body."""

    id: str = Field(description='对象 id(之后 ring / underline / erase 靠它引用)')
    at: str = Field(description='落笔点: "0.12,0.28"(归一化) 或 "below:a1"')
    size: str = Field(default='m', description='字号 s / m / l')


class BbWrite(CtmlRoutine, BbCommand):
    """写一行字: 落笔点给定, 宽度和高度由黑板侧量出来回灌."""

    name = 'bb_write'

    meta: ClassVar[Dict[str, Any]] = {
        **BbCommand.meta,
        # 收 body(默认是不收, 见 ctml.CtmlRoutine): 要写的那一行写在标签中间.
        'body': True,
        'description': (
            '在黑板写一行字: body = 这一行原文, at = 落笔点'
            '(坐标 "x,y" 或 "below:a1"), size = 字号 s/m/l; '
            '写完等这一行画完才回执'),
        'example': (
            'at 是"基线"(字的落点), 不是包围盒顶边: at="0.12,0.28" 的字'
            '坐在 28% 高度上.\n'
            '一次一行 —— 要写三行就发三次; body 里换行会当场报错.\n'
            'id 自己起(a1, a2…), 之后 ring/underline 靠它引用.'
        ),
        'input_schema': BbWriteInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = parse(BbWriteInput, kwargs)
        ident = obj_id(inp.id)
        at = anchor(inp.at, 'at')
        size = size_of(inp.size)
        await self._body_ready.wait()
        text = self.body_text.strip()
        if not text:
            raise RuntimeError(
                'bb_write 的 body 是空的: 要写的字放在标签中间')
        if '\n' in text:
            raise RuntimeError(
                'bb_write 一次只写一行: body 里不许换行, 多行就发多个 bb_write')
        if len(text) > _MAX_CHARS:
            raise RuntimeError(
                f'一行 {len(text)} 字太长了(上限 {_MAX_CHARS}): 拆成几次 bb_write')
        r = await self._draw({'do': 'write', 'text': text,
                              'at': at, 'size': size}, ident)
        # 回执给模型的那一行只有 id: 它刚写的内容自己知道, 而 id 是后续
        # 挂靠(below:/around:)的唯一抓手. 原文留给 details(UI/排障).
        rid = r.get('id') or ident
        return {'content': f'id={rid}',
                'details': {'id': rid, 'text': text}}
