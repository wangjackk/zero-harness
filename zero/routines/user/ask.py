"""Ask -- 给用户发单选题/日期选择并等结果的 UI routine.

两种交互模式(由 ``kind`` 选择):

- ``selector``(默认):单选题.``props.options`` 给选项,``props.allow_other`` 允许自由输入.
- ``date``:日期选择器.选一个日期,返回 ``YYYY-MM-DD`` 字符串.
  ``props.min`` / ``props.max`` / ``props.default`` 约束范围与默认值(ISO 日期字符串).

用法::

    # 代码:
    value = await self.call('ask', kwargs={
        'question': '选哪个?',
        'props': {'options': ['a', 'b'], 'allow_other': False},
    })
    # 日期:
    value = await self.call('ask', kwargs={
        'kind': 'date', 'question': '你生日?',
        'props': {'min': '1900-01-01'},
    })

"""
from __future__ import annotations

import json
from typing import Any, ClassVar, Dict, Literal

from pydantic import BaseModel, Field
from routine import Routine

_BRIDGE_NAME = 'web_server'
_REQ_TIMEOUT_MARGIN = 10.0


class AskInput(BaseModel):
    question: str = Field(description='要问用户的问题文本')
    kind: Literal['selector', 'date'] = Field(
        default='selector',
        description='交互模式:selector(单选,默认)/ date(日期选择)',
    )
    props: Dict[str, Any] = Field(
        default_factory=dict,
        description='组件具体参数,跟前端 component props 对齐.selector: '
                    '{options: list[str]|str(必填), allow_other?: bool}; '
                    'date: {min?: str, max?: str, default?: str} (ISO YYYY-MM-DD)',
    )
    timeout: float = Field(default=300, description='等待用户回答的超时秒数')


class AskOutput(BaseModel):
    answer: str = Field(description='用户选择的选项值 / 自由输入内容 / 日期 YYYY-MM-DD')


class Ask(Routine):
    """给用户发一个单选题或日期选择,并等待结果.

    selector 模式(默认):句中有逗号时,把属性值改成单引号包裹:
        props.options='["hi,jack", "var-123", "var_123"]'

    date 模式:返回 YYYY-MM-DD 字符串,可经 props.min/max/default 约束.
    """

    meta: ClassVar[Dict[str, Any]] = {
        'description': '给用户发单选题(selector)或日期选择器(date)并等结果; selector 支持 allow_other 自由输入.',
        'input_schema': AskInput.model_json_schema(),
        'output_schema': AskOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        # XML 调用方只能写平铺属性(<ask options="a,b"/>),parser 生成平铺 kwargs.
        # 把组件专属参数收进 props,跟代码调用方统一.
        _COMPONENT_KEYS = {'options', 'allow_other', 'min', 'max', 'default'}
        if not kwargs.get('props') and any(k in kwargs for k in _COMPONENT_KEYS):
            kwargs = {
                **kwargs,
                'props': {k: kwargs[k] for k in _COMPONENT_KEYS if k in kwargs},
            }
        inp = AskInput(**kwargs)
        question = inp.question
        timeout = inp.timeout
        kind = inp.kind
        props = inp.props

        bridge_id = await self._find_bridge_id()
        if bridge_id is None:
            raise RuntimeError(f'ask: bridge routine {_BRIDGE_NAME!r} not running')

        if kind == 'date':
            return await self._ask_date(bridge_id, question, props, timeout)

        # selector 模式(默认)
        options = props.get('options')
        if options is None:
            raise ValueError("ask: selector 模式 props 必须提供 options")
        parsed = self._parse_options(options)
        allow_other = bool(props.get('allow_other', False))
        self._logger.info('ask question=%r options=%s allow_other=%s', question, parsed, allow_other)

        # 不吞异常:超时 / 出错直接向上传播,让调用方决定语义(例如审批节点
        # 必须 fail-closed,不能把超时误判成有效答案).
        try:
            result = await self.req(
                bridge_id, 'ui_request',
                {
                    'component': 'selector',
                    'props': {
                        'question': question,
                        'options': parsed,
                        'allow_other': allow_other,
                        'timeout': timeout,
                    },
                    'timeout': timeout,
                },
                timeout=timeout + _REQ_TIMEOUT_MARGIN,
            )
        except Exception as exc:
            self._logger.error('ask error: %s', exc)
            raise

        return await self._extract_value(result)

    async def _ask_date(
        self, bridge_id: str, question: str, props: Dict[str, Any], timeout: float,
    ) -> str:
        """date 模式:发日期选择器,返回 YYYY-MM-DD 字符串."""
        min_date = props.get('min')
        max_date = props.get('max')
        default_date = props.get('default')
        self._logger.info(
            'ask date question=%r min=%s max=%s default=%s',
            question, min_date, max_date, default_date,
        )
        try:
            result = await self.ctx.req(
                bridge_id, 'ui_request',
                {
                    'component': 'date',
                    'props': {
                        'question': question,
                        'min': min_date,
                        'max': max_date,
                        'default': default_date,
                        'timeout': timeout,
                    },
                    'timeout': timeout,
                },
                timeout=timeout + _REQ_TIMEOUT_MARGIN,
            )
        except Exception as exc:
            self._logger.error('ask date error: %s', exc)
            raise
        value = await self._extract_value(result)
        # 统一成 YYYY-MM-DD(前端 NDatePicker 可能返回时间戳或 ISO 串)
        return self._normalize_date(value)

    async def _extract_value(self, result: dict) -> str:
        """从 bridge 回执里取出用户值,失败则抛异常(不吞错)."""
        if not result.get('ok'):
            err = str(result.get('error') or 'unknown ui_request error')
            if result.get('timed_out'):
                self._logger.warning('ask timeout')
                raise TimeoutError(err)
            raise RuntimeError(err)
        value = result.get('value')
        self._logger.info('ask answer=%r', value)
        return str(value)

    @staticmethod
    def _normalize_date(value: Any) -> str:
        """把前端返回的日期值归一化成 YYYY-MM-DD 字符串.

        NDatePicker 的 value 可以是时间戳(ms, int)或 ISO 字符串;统一成
        'YYYY-MM-DD'.无法解析时原样返回(交给调用方校验).
        """
        if value is None:
            return ''
        s = str(value).strip()
        if not s:
            return ''
        # 纯数字 -> 毫秒时间戳
        if s.isdigit():
            import datetime as _dt
            try:
                ts = int(s)
                # 兼容秒 / 毫秒
                if ts > 10_000_000_000:
                    ts //= 1000
                return _dt.datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
            except (ValueError, OSError):
                return s
        # ISO 字符串 -> 截取日期部分
        if 'T' in s or len(s) >= 10:
            return s[:10]
        return s

    async def _find_bridge_id(self) -> str | None:
        """查 WebServer routine 当前 running 实例的 id(跨进程正确).

        WebServer 是独立 passive routine(可能跑在另一个进程),ask 不持有它的
        handle.id,故经 ``ctx.get_running_routines`` 查.bridge 还没起来时
        返回 None--调用方据此报错.
        """
        try:
            routines = await self.ctx.get_running_routines()
        except Exception as exc:
            self._logger.warning('ask: get_running_routines failed: %r', exc)
            return None
        for r in routines:
            if str(r.get('name') or '') == _BRIDGE_NAME:
                rid = str(r.get('id') or '').strip()
                if rid:
                    return rid
        return None

    @staticmethod
    def _parse_options(options: list[str] | str) -> list[str]:
        if isinstance(options, list):
            return [str(item) for item in options]
        text = str(options).strip()
        if not text:
            raise ValueError('options 不能为空')
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = [item.strip() for item in text.split(',') if item.strip()]
        if not isinstance(parsed, list) or not parsed:
            raise ValueError('options 必须是非空数组或可按逗号分隔的字符串')
        return [str(item) for item in parsed]

