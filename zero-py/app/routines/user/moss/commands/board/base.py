"""board 族的公共薄壳: 找那块板 + 交一笔 + 等这一笔画完.

十三扇门共享的就这几件事, 各自只声明自己的入参和示例.

分族判据是**对象**: 族内每一笔都落在**同一块黑板**上 —— 所以这里没有
"哪块板"的参数(板由装配决定), 与 chan 族正好相反(chan 每笔针对不同的轨,
都要 name).

为什么一 do 一扇门而不是一个 ``<bb do="..."/>``: 十二个 do 的字段几乎不重叠
(write 要 text/at/size, line 要 from/to/dash, rect 要 at/w/h/round), 名字
写死, 模型不必记"哪个 do 要哪些键"; 更要紧的是**只有 write / pixel 收 body**
—— 而 body 有无是**逐类声明**的(``meta['body']``, 默认不收, 见
``ctml.CtmlRoutine``; code 面 ``call(body=…)`` 的闸门读它; 提示词侧不印这个
旗, 见 ``prompt/capability``), 做成一个带 do 的 routine 就只能整族全收 ——
于是 ``do="circle"`` 那段 body 没人读.

**两种量两个单位**(最容易画歪的一处, 校验都在这里):
- 位置 = 归一化 0~1, x 按画布宽、y 按画布高;
- 长度 = **画布短边**为单位(r / rx / ry / w / h)。长度不能按轴归一化, 否则
  ``w=0.3 h=0.3`` 在宽屏上是长方形、圆会压成横椭圆;
- ``pad`` / ``round`` 是**画布像素**(整数), 与上面两种都不同。
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict, List, Optional, Union

from pydantic import ValidationError
from routine.routine import Routine

from ...board import timing

__all__ = ['BbCommand', 'parse', 'anchor', 'anchors_of', 'obj_id',
           'size_of', 'len_of', 'px_of', 'flag_of']

#: 锚点前缀 —— 黑板侧只认这四个; 别的写法(含拼错)会被它**静默当 below**
_REF = ('right-of', 'left-of', 'above', 'below')
_SIZE = ('s', 'm', 'l')


class BbCommand(Routine):
    """黑板一笔: 交给 board, 等它画完再回执(标签顺序 = 画面顺序)."""

    #: 全族出厂声明 ``observe``(与 chan 族同一条路, 声明跟着动作走): 画面是
    #: 模型看不见的那部分世界 —— 这一笔画成什么样、板上有哪些 id, 只有回执能
    #: 告诉它. 不点灯的话, 画完那一拍就白画了(下一拍得自己想起来去看).
    #: 族里没有例外(``bb_objects`` 是"答案本身", 其余是"世界变了"), 与
    #: ``chan_clear`` 那种中断类不同: 黑板不打断任何东西, 擦除也是世界变了.
    meta: ClassVar[Dict[str, Any]] = {
        'observe': True,
    }

    async def _board_rid(self) -> str:
        """那块板的实例 id(按名自解析, 同 chan 找 shell)."""
        for r in await self.get_running_routines():
            if r.get('name') == 'board':
                return r['id']
        raise RuntimeError('routine not running: board')

    async def _draw(self, act: Dict[str, Any],
                    ident: str = '') -> Dict[str, Any]:
        """推一笔并等它画完.

        等的是**入场动画时长**(见 ``board.timing``): 不等的话, 同一条轨上
        连着的几笔会在黑板侧同时开画(协议: 一条消息 = 同时开始), 画面糊在
        一起. 等完再返回, 下一个标签才接得上 —— 顺序由轨道表达, 不由 sleep
        命令表达.

        ``ident`` 只对"之后还要引用"的对象有意义(write / line); 其余留空,
        由 board 编号(x1, x2…) —— 那些笔不进对象表, 起了名字也没人指得着.
        """
        rid = await self._board_rid()
        payload: Dict[str, Any] = {'act': act}
        if ident:
            payload['id'] = ident
        r = await self.req(rid, 'act', payload)
        await asyncio.sleep(timing.estimate(act))
        return r

    async def stop(self) -> None:
        pass

    async def _known(self, ident: str) -> Optional[bool]:
        """这个 id 在黑板上吗? ``None`` = 还没收到过快照, 判断不了.

        黑板对"引用不存在的 id"是静默的(圈落画布中心, 擦什么都不发生),
        所以在有快照的前提下先查一眼 —— 但**没有快照时不许判死**: 服务端
        不缓存历史, 刚连上时本来就什么都没有.
        """
        r = await self.req(await self._board_rid(), 'objects', {})
        objs = (r or {}).get('objects') or []
        if not objs:
            return None
        return any(o.get('id') == ident for o in objs)


def parse(model: Any, kwargs: Dict[str, Any]) -> Any:
    """校验入参, 缺字段时换成能照着做的一句话.

    pydantic 的原文("Field required")太干, 而这些动词**唯一天然报错的地方**
    就是这里(黑板对坏 payload 全静默), 所以错误必须一眼能改。字段名保留,
    因为要改的就是那个属性。
    """
    try:
        return model.model_validate(kwargs or {})
    except ValidationError as exc:
        bits = []
        for err in exc.errors()[:3]:
            name = '.'.join(str(x) for x in err['loc']) or '参数'
            if err['type'] == 'missing':
                bits.append(f'缺 {name}')
            else:
                bits.append(f'{name} 不合法({err["msg"]})')
        raise RuntimeError(
            '入参不对: ' + '; '.join(bits)
            + ' —— 每个参数怎么写在能力名片里那个名字的括号里') from None


# ── 入参校验(黑板不校验 payload, 这一层是唯一的报错处) ────────────

def anchor(raw: Any, field: str) -> Union[List[float], str]:
    """``"0.12,0.28"`` → [x, y]; ``"below:a1"`` → 原样; 其它一律报错.

    黑板对这两类失败都是静默的: 前缀拼错当 below, id 不存在落画布中心.
    所以这里宁可报错 —— 错误帧会带注意债, 模型当拍就能改.
    """
    s = str(raw or '').strip()
    if not s:
        raise RuntimeError(
            f'{field} 必填: 坐标写 "0.12,0.28", 挂靠写 "below:a1"')
    if ':' in s:
        head, _, ref = s.partition(':')
        head, ref = head.strip().lower(), ref.strip()
        if head not in _REF:
            raise RuntimeError(
                f'{field} 的前缀只认 {" / ".join(_REF)}(写错会被黑板当 below): '
                f'你写的是 {head!r}')
        if not ref:
            raise RuntimeError(f'{field} 少了对象 id: 例如 "below:a1"')
        return f'{head}:{ref}'
    parts = [p.strip() for p in s.split(',')]
    if len(parts) != 2:
        raise RuntimeError(f'{field} 的坐标要两段 "x,y"(归一化 0~1): {s!r}')
    try:
        x, y = float(parts[0]), float(parts[1])
    except ValueError:
        raise RuntimeError(f'{field} 的坐标不是数字: {s!r}') from None
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise RuntimeError(f'{field} 的坐标是归一化值(0~1): {s!r}')
    return [x, y]


def obj_id(raw: Any, field: str = 'id') -> str:
    """对象 id: 唯一、稳定、不复用 —— 之后 circle / erase 靠它引用."""
    s = str(raw or '').strip()
    if not s:
        raise RuntimeError(
            f'{field} 必填: 起个短名字(如 a1), 之后 ring/erase 靠它引用')
    if len(s) > 40 or any(c.isspace() for c in s):
        raise RuntimeError(f'{field} 只能是一段不含空白的短名字: {s!r}')
    return s


def size_of(raw: Any) -> str:
    s = str(raw or 'm').strip().lower()
    if s not in _SIZE:
        raise RuntimeError(f'size 只认 {" / ".join(_SIZE)}: {s!r}')
    return s


def px_of(raw: Any, field: str, default: int, hi: int = 200) -> int:
    """画布像素量(pad / round): 整数, 与归一化无关."""
    s = str(raw if raw is not None else '').strip()
    if not s:
        return default
    try:
        v = int(round(float(s)))
    except ValueError:
        raise RuntimeError(f'{field} 是画布像素(整数, 不是比例): {s!r}') from None
    if not 0 <= v <= hi:
        raise RuntimeError(f'{field} 是画布像素, 合理范围 0~{hi}: {v}')
    return v


def len_of(raw: Any, field: str, default: Optional[float] = None) -> float:
    """长度: 以**画布短边**为单位(0~1), 不是按轴归一化.

    黑板侧拿短边折算像素, 所以 ``r=0.3`` 在宽屏上仍是圆的半径。写成按轴归一化
    的话 ``w=h=0.3`` 会是个长方形 —— 这是接口里最容易画歪的一处, 所以这里
    只收 0~1 的短边比例。
    """
    s = str(raw if raw is not None else '').strip()
    if not s:
        if default is None:
            raise RuntimeError(
                f'{field} 必填: 以画布短边为单位, 例如 {field}="0.08"')
        return default
    try:
        v = float(s)
    except ValueError:
        raise RuntimeError(
            f'{field} 是数字(画布短边的比例, 如 0.08): {s!r}') from None
    if not 0 < v <= 1.0:
        raise RuntimeError(
            f'{field} 以画布短边为单位, 合理范围 0~1: {v}')
    return v


def anchors_of(raw: Any, field: str) -> List[Union[List[float], str]]:
    """一串锚点: 空白或分号分隔.

    ``points="0.5,0.16 0.32,0.52 0.68,0.52"`` —— 属性装不下数组, 这是唯一为
    属性扩的语法; 里面每一段仍是同一个锚点写法(坐标 ``x,y`` 或 ``below:a1``),
    所以模型只需会一种。
    """
    s = str(raw or '').strip()
    if not s:
        raise RuntimeError(f'{field} 必填: 至少两个锚点, 空白分隔')
    parts = [p for p in s.replace(';', ' ').split() if p]
    if len(parts) < 2:
        raise RuntimeError(
            f'{field} 至少两个锚点(空白分隔): {s!r}')
    return [anchor(p, field) for p in parts]


def flag_of(raw: Any, field: str) -> bool:
    s = str(raw or '').strip().lower()
    if s in ('', 'false', '0'):
        return False
    if s == 'true':
        return True
    raise RuntimeError(f'{field} 只认 "true"(不写就是 false): {s!r}')
