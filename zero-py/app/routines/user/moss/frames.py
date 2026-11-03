"""帧的词汇表: 回执载荷的三个字段 + "帧 → 一行文本"的唯一写法.

帧分**两层**, 谁也别越界:

- **信封**(``channel`` 写, ``shell`` 记账): ``kind`` = 成没成(action /
  error), ``verb``/``chan``/``rid`` = 谁在哪条轨, ``owed`` = 要不要点灯.
- **载荷**(routine 的 ``run`` 返回): ``content`` = 模型读的一行,
  ``details`` = 人看的结构化数据, ``observe`` = 结果自报.

失败没有载荷 —— 门做不到它声称要做的事就**抛异常**, 信封改写成
``kind='error'`` 并把原因放 ``text``. 于是"成没成"永远只有一个判据
(信封), 载荷只回答"结果说了什么". ``text`` 由 ``failure`` 一处成形
(对面就是 ``receipt``), 校验错误在那里脱掉 pydantic 的外衣.

``text`` 的读者是**模型**, 所以异常那一句只说"结果怎么样", 不说内部
术语. 回执不合契约是 routine 自己的 bug, 模型改不了它, 也永远碰不到
``content``/``details`` 这两个名字 —— 精确原因(多了哪些键、哪个字段
不可序列化)写进日志给改 routine 的人; 见 ``_broken``.

这个分工的用处, 一句话: **``details`` 没有通往 prompt 的路**. ``bb_objects``
那种"整张表连 17 位小数灌进上下文"的事故, 不是靠截断拦住的, 是靠**没有
字段可灌** —— 模型那一行只取 ``content``, 而 ``content`` 是门自己写的一句话.
``details`` 照旧进 Moment(排障要原文)、进 UI, 一分不少.

唯一的例外是 ``images``: 那是**图**, 不是字. 它没法写成 ``content`` 那句
文本(一张图 base64 起来几十万字符, 灌进一行等于把上下文烧光), 只能以
多模态 block 的形式附在消息上 —— 所以给它一个字段, 而不是让每个看得见图
的 routine 各自往 ``content`` 里塞路径再让下游去猜. 判据仍是同一条:
**模型能拿到的非文本内容, 只有这一个字段, 且只有它声明要图才算数**.

``content`` 缺省 ``'ok'``: 琐碎的成功(写完了/删掉了)不必每扇门都编一句话,
光秃秃的 ``bb_underline: ok`` 模型照样知道成了.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from routine.logger import setup_logger

__all__ = ['RECEIPT_KEYS', 'receipt', 'failure', 'frame_line']

_log = setup_logger('moss.frames')

#: 回执载荷只认这四个键(信封的键由 channel/shell 自己写)
RECEIPT_KEYS = frozenset({'content', 'details', 'observe', 'images'})

#: ``content`` 的兜底上限. 门自己知道该留多长(content 是它写的), 这条线
#: 只防"哪个门不小心把大件塞进 content" —— 事故要看得见, 但不能撑爆一拍.
FRAME_CLIP = 4000


def _broken(verb: str, why: str) -> RuntimeError:
    """回执不合契约: 原因进日志, 帧里那句话只说"这个能力没交回结果".

    两个读者别混: 日志是给**写 routine 的人**的(多了哪些键、字段怎么不
    可序列化), 帧是给**模型**的. 模型动不了 routine 的返回结构, 把契约
    字段名写进它的输入只会教它复读内部词汇, 再顺手重试一次同样会失败
    的调用 —— 所以那一句直说它自身出错, 别重试.

    帧上的能力名由 ``frame_line`` 拼(``error(verb): ...``), 这里不再重复.
    """
    _log.error('%s: 回执不合契约 —— %s', verb, why)
    return RuntimeError('这个能力没交回结果(它自身出错, 重试也一样)')


def receipt(verb: str, result: Any) -> Dict[str, Any]:
    """``run`` 的返回 → 回执载荷 ``{content, details, images?, observe?}``.

    **唯一的校验处**: 四个字段之外的键当场炸 —— 变成错误帧, 当拍就看得见.
    悄悄收下才是最坏的结果: 一个拼错的键会安静地消失, 模型拿着残缺的
    回执猜它想说什么.

    ``images`` 是本地路径列表(读图类 routine 交回的"图本身"), 组装成
    多模态 block 的责任在 ``brain.input_items`` —— 这一层只校验它长得像
    不像路径列表: 塞进来一个 dict 或空串, 到那一层就是 ``resolve_data_url``
    读不出东西、图静默消失, 模型看到"图已附上"却什么也没看见.

    ``details`` 在这里试一次 ``json.dumps``: 不可序列化 = 回执过不去传输层,
    而 ``Channel._report`` 发不出去只记日志 —— 模型会永远等一条不存在的
    回执. 宁可当场变错误帧.
    """
    if result is None:      # 什么都没说 = 成了, 没什么可报
        return {'content': 'ok', 'details': {}}
    if not isinstance(result, dict):
        raise _broken(verb, f'run 返回 {type(result).__name__}, 契约要 dict')
    extra = set(result) - RECEIPT_KEYS
    if extra:
        raise _broken(
            verb, f'多了 {", ".join(sorted(extra))} '
                  f'(载荷只认 content/details/images/observe, 额外结果塞进 details)')
    content = result.get('content')
    details = result.get('details')
    out: Dict[str, Any] = {'content': str(content) if content else 'ok',
                           'details': {} if details is None else details}
    images = result.get('images')
    if images is not None:      # None = 没有图, 与"不放这个键"同义
        if not isinstance(images, (list, tuple)):
            raise _broken(verb, f'images 要路径列表, 给了 {type(images).__name__}')
        paths = [str(p) for p in images if p]
        if not paths:
            raise _broken(verb, 'images 给了空列表 —— 要么不放这个键, 要么放路径')
        out['images'] = paths
    if result.get('observe'):
        out['observe'] = True
    try:
        json.dumps(out['details'])
    except (TypeError, ValueError) as exc:
        raise _broken(verb, f'details 不可序列化({exc})') from None
    return out


#: pydantic 给自定义校验器那句话包的前缀
_VALUE_PREFIX = 'Value error, '

#: 常见校验错误 → 模型看得懂的一句话(ctx 里是阈值 / 候选值)
_FIELD_MSG = {
    'missing': '缺这个参数',
    'literal_error': '只能是 {expected}',
    'less_than_equal': '要 ≤ {le}',
    'greater_than_equal': '要 ≥ {ge}',
    'int_parsing': '要是整数',
    'int_type': '要是整数',
    'int_from_float': '要整数(别带小数)',
    'float_parsing': '要是数字',
    'float_type': '要是数字',
    'string_type': '要是字符串',
}


def failure(exc: BaseException) -> str:
    """异常 → 错误帧那一行. ``receipt`` 的对面: 成了走 receipt, 败了走这里.

    要脱的只有 **pydantic 的外衣**::

        1 validation error for CarShakeInput
        Value error, x/y 模式 amp 须 <= 0.6 m/s
          [type=value_error, input_value={'mode': 'y', ...}, input_type=dict]
        For further information visit https://errors.pydantic.dev/2.13/v/value_error

    类名、整份入参 dump、文档链接 —— 全是给 debug 代码的人看的; 模型读了
    只会复读它, 而它真正能动的只有**哪个字段、允许什么、给了什么**. 作者
    自己抛的异常原样穿过(那句话本来就是人话写的).

    能力名由 ``frame_line`` 拼, 这里不带.
    """
    from pydantic import ValidationError

    if not isinstance(exc, ValidationError):
        return str(exc)
    errs = exc.errors()
    parts: List[str] = []
    for err in errs[:4]:
        loc = '.'.join(str(x) for x in (err.get('loc') or ()))
        msg = str(err.get('msg') or '')
        if msg.startswith(_VALUE_PREFIX):
            msg = msg[len(_VALUE_PREFIX):]
        ctx = err.get('ctx') or {}
        # 阈值印成 2 而不是 2.0(签名里也是 `<=2`)
        ctx = {k: (f'{v:g}' if isinstance(v, float) else v)
               for k, v in ctx.items()}
        tpl = _FIELD_MSG.get(str(err.get('type')))
        if tpl:
            try:
                msg = tpl.format(**ctx)   # 模板要的键不在 ctx 里就留原话
            except KeyError:
                pass
        got = err.get('input')
        if loc and isinstance(got, (int, float, str, bool)):
            shown = f'{got:g}' if isinstance(got, float) else str(got)
            msg += f'(给了 {shown[:40]})'
        parts.append(f'{loc}: {msg}' if loc else msg)
    if len(errs) > 4:
        parts.append(f'…共 {len(errs)} 处不对')
    return '参数不对 —— ' + '; '.join(parts)


def _clip(text: str, limit: int = FRAME_CLIP) -> str:
    if len(text) <= limit:
        return text
    return f'{text[:limit]}…(截断, 共 {len(text)} 字)'


def frame_line(f: Dict[str, Any]) -> str:
    """观测帧 → 那一行进 prompt 的文本.

    只读信封的 ``kind``/``verb`` 与载荷的 ``content`` —— ``details`` 在这
    里是**看不见的**, 这就是它进不了上下文的原因. error 帧的尖括号换全角:
    错误里的标签名进 LLM 输入后只可读不可抄(防回声帧复读).
    """
    kind = f.get('kind')
    verb = f.get('verb')
    if kind == 'action':
        r = f.get('result')
        text = r.get('content') if isinstance(r, dict) else ''
        return f"{verb}: {_clip(str(text or 'ok'))}"
    if kind == 'error':
        head = f'error({verb})' if verb else 'error'
        text = str(f.get('text', '')).replace('<', '〈').replace('>', '〉')
        return f'{head}: {_clip(text)}'
    return _clip(str(f.get('text', '')))   # 其他 kind 原样文本化
