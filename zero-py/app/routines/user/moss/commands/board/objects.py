"""bb_objects —— 看黑板上现在有什么(对象表**清单**, 不是转储).

回执会进上下文(观测帧 → 下一拍的 prompt), 所以这里给的是**事实摘要**:
谁在板上(id / kind / 文字开头), 不是黑板内部状态。黑板的原始对象表里
每个对象还带一整套全精度 bbox(17 位小数的那种)和 socket id —— 那是渲染
细节, 对"板上有哪些东西、我要挂靠谁"没有用, 塞进 prompt 纯属浪费.

真要几何(box="true")时才给 bbox, 而且千分位截断: 归一化坐标到 0.001
已是画布上约两个像素, 再多的位数没有意义.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel, Field

from .base import BbCommand, flag_of, parse

__all__ = ['BbObjects']

#: 每条文字在清单里留多少字 —— 认得出是哪句话就够了, 原文在它自己手里
_TEXT_MAX = 30

#: 模型那一行最多列多少个对象(多的折成"…还有 N 个")
_LIST_MAX = 8


def _clip(text: str) -> str:
    return text if len(text) <= _TEXT_MAX else text[:_TEXT_MAX] + '…'


class BbObjectsInput(BaseModel):
    """默认只看清单; 要几何才写 box="true"."""

    box: str = Field(default='', description='要 bbox 才写 "true"')


class BbObjects(BbCommand):
    """读对象表: 清单(id / kind / 文字), 再要才有几何."""

    name = 'bb_objects'

    meta: ClassVar[Dict[str, Any]] = {
        **BbCommand.meta,
        'description': ('看黑板现在有什么: 对象清单(id / kind / 文字开头). '
                        '默认不给坐标, 要 bbox 写 box="true". write / line / '
                        'rect / poly / circle / ellipse 进表; arrow / ring / '
                        'underline / point / pixel 不进'),
        'example': (
            'count=0 = 板还空着, 或者刚连上还没收到过上报'
            '(黑板不缓存历史, 要等下一条动作才触发上报).\n'
            'box 是 [x, y, w, h](归一化, x 按画布宽、y 按画布高), '
            '黑板侧量出来的(依赖真实字体), 只到千分位.'),
        'input_schema': BbObjectsInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = parse(BbObjectsInput, kwargs)
        want_box = flag_of(inp.box, 'box')
        r = await self.req(await self._board_rid(), 'objects', {})
        rows: List[Dict[str, Any]] = []
        for o in r.get('objects') or []:
            row: Dict[str, Any] = {'id': o.get('id') or '',
                                   'kind': str(o.get('kind') or '')}
            text = str(o.get('text') or '').strip()
            if text:
                row['text'] = _clip(text)
            box = o.get('box')
            if want_box and isinstance(box, list) and len(box) == 4:
                row['box'] = [round(float(v or 0), 3) for v in box]
            rows.append(row)
        return {'content': _summary(rows, want_box, r.get('connected')),
                'details': {'connected': bool(r.get('connected')),
                            'count': len(rows), 'objects': rows}}

    # 模型那一行(_summary)与原始表(details)分开: 表里每个 stroke 的
    # 全精度 bbox 与 socket 编号, 只有排障时有人看, 不该进上下文.


def _summary(rows: List[Dict[str, Any]], want_box: bool,
             connected: Any) -> str:
    """对象清单 → 模型读的那一行: `id「文字」@x,y,w,h, ...`.

    ``box="true"`` 时把几何也写进来 —— 模型要的东西必须在 content 里,
    ``details`` 它看不见. 清单本身按 ``_LIST_MAX`` 条封顶, 免得一板
    二十个对象把那行撑成一段.
    """
    if not rows:
        return '板是空的' if connected else '黑板没连上(服务端没起?)'
    bits: List[str] = []
    for row in rows[:_LIST_MAX]:
        seg = str(row.get('id') or '')
        if row.get('text'):
            seg += f'「{row["text"]}」'
        else:
            seg += f'({row.get("kind") or "?"})'
        box = row.get('box')
        if want_box and box:
            seg += '@' + ','.join(f'{v:g}' for v in box)
        bits.append(seg)
    more = '' if len(rows) <= _LIST_MAX else f' …还有 {len(rows) - _LIST_MAX} 个'
    return f'{len(rows)} 个: ' + ', '.join(bits) + more
