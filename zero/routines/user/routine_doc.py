"""routine_doc ---- 查看 routine 的精简文档(schema 翻译成人类可读).

比 list_routines 的原始 schema 更精简:签名 + 参数列表.无参数的 routine 只列签名.

调用示例:
    curl -XPOST localhost:7780/run/routine_doc -d '{"name":"list_routines"}'
    run_routine({name: 'routine_doc', kwargs: {name: 'list_routines'}})
"""
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine import Routine


class RoutineDocInput(BaseModel):
    """routine_doc 输入:必须指定 name."""

    name: str = Field(
        description='要查看文档的 routine name',
    )


class RoutineDocOutput(BaseModel):
    doc: str = Field(
        default='',
        description='routine 的精简文档(签名 + 参数列表)',
    )


class RoutineDoc(Routine):
    """查看 routine 的精简文档(schema 翻译成人类可读)."""

    meta: ClassVar[Dict[str, Any]] = {
        'description': '查看 routine 文档(schema 翻译成精简可读格式).必须指定 name',
        'input_schema': RoutineDocInput.model_json_schema(),
        'output_schema': RoutineDocOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = RoutineDocInput.model_validate(kwargs)
        try:
            routines = await self.get_routines()
        except NotImplementedError:
            return {'error': 'get_routines not implemented for this transport', 'doc': ''}
        except Exception as exc:
            self._logger.warning('routine_doc failed: %s', exc)
            return {'error': f'{type(exc).__name__}: {exc}', 'doc': ''}
        routines = [r for r in routines if r.get('name') == inp.name]
        if not routines:
            return {'error': f'routine not found: {inp.name}', 'doc': ''}
        self._logger.info('routine_doc: %s', inp.name)
        return {'doc': _render_doc(routines[0])}


def _render_doc(routine: Dict[str, Any]) -> str:
    """把单个 routine 的 meta(input_schema + description)翻译成精简文档.

    格式::

        routine: list_routines
        列出 routine(已注册 / 运行中).kind=registered(默认)|running
        参数:
        - kind (string) 可选 默认 registered - registered=已注册(默认); running=运行中

    无参数的 routine 只列 name + description.
    """
    name = routine.get('name', '') or '?'
    meta = routine.get('meta') or {}
    desc = meta.get('description') or ''
    schema = meta.get('input_schema') or {}
    props = schema.get('properties') or {}
    required = set(schema.get('required') or [])

    lines = [f'routine: {name}']
    if desc:
        lines.append(desc)
    if props:
        lines.append('参数:')
        defs = schema.get('$defs') or {}
        for pname, pschema in props.items():
            pschema = pschema if isinstance(pschema, dict) else {}
            parts = [f'- {pname}']
            ptype, enum_vals = _resolve_type(pschema, defs)
            if ptype:
                parts.append(f'({ptype})')
            if pname in required:
                parts.append('必填')
            elif 'default' in pschema:
                parts.append(f'默认 {_fmt_default(pschema["default"])}')
            if enum_vals:
                parts.append(f'[{" / ".join(enum_vals)}]')
            pdesc = (pschema.get('description') or '').strip()
            if pdesc:
                parts.append(f'- {pdesc}')
            lines.append(' '.join(parts))
    return '\n'.join(lines)


def _resolve_type(pschema: Dict[str, Any], defs: Dict[str, Any]) -> tuple[str, list[str]]:
    """从 schema property 解析类型名(处理 $ref / anyOf / oneOf).

    返回 (type, enum_vals):enum_vals 非空时是 $ref 指向的 enum 定义的可选值列表.
    """
    t = pschema.get('type')
    if t:
        return t, list(pschema.get('enum') or [])
    # $ref: 往 $defs 查.常见于 str-enum:type=string + enum=[...].
    ref = pschema.get('$ref')
    if ref and ref.startswith('#/$defs/'):
        def_name = ref[len('#/$defs/'):]
        defn = defs.get(def_name) or {}
        return defn.get('type', 'string'), list(defn.get('enum') or [])
    for combo in ('anyOf', 'oneOf'):
        opts = pschema.get(combo) or []
        for opt in opts:
            if isinstance(opt, dict) and opt.get('type') != 'null':
                return opt.get('type', 'string'), list(opt.get('enum') or [])
    return 'string', []


def _fmt_default(v) -> str:
    """int-like float 去尾零(pydantic 把 10 标准化成 10.0 时还原)."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if v is None:
        return 'null'
    return str(v)
