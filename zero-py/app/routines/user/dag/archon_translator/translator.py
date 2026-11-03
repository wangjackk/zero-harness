"""Archon 工作流 YAML → 本地 DAG YAML 翻译器.

节点类型映射
─────────────────────────────────────────────────────────────────
Archon 写法                 本地 routine       核心 inputs
─────────────────────────────────────────────────────────────────
prompt: "..."           →   run_llm            prompt
command: name           →   run_llm            system=<文件内容>, prompt
bash: "cmd"             →   run_bash           cmd
script: code/name bun   →   run_script         code/file, runtime
loop: {prompt,until}    →   run_llm            prompt(单轮近似,加警告)
approval:               →   dag_approval       message, capture_response
─────────────────────────────────────────────────────────────────

变量替换
─────────────────────────────────────────────────────────────────
$ARGUMENTS      → $WI.arguments     (自动在 DAG inputs 中声明)
$USER_MESSAGE   → $WI.user_message
$BASE_BRANCH    → $WI.base_branch
$WORKFLOW_ID    → (剥离,加注释)
$ARTIFACTS_DIR  → (剥离,加注释)
$nodeId.output  → 不变(连字符 ID 本地支持)
─────────────────────────────────────────────────────────────────

模型名映射
─────────────────────────────────────────────────────────────────
haiku / sonnet / opus / claude-* / anthropic/* → 'claude'
无模型声明                                      → 不填(用系统默认)
其他 provider(codex/pi/opencode)              → 'claude'(加警告)
─────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ── 警告等级 ──────────────────────────────────────────────────────────────────

class WarnLevel:
    INFO  = 'info'
    WARN  = 'warn'
    ERROR = 'error'


@dataclass
class TranslateWarning:
    node_id: str | None
    level: str
    message: str

    def __str__(self) -> str:
        loc = f'[{self.node_id}] ' if self.node_id else ''
        return f'{self.level.upper()} {loc}{self.message}'


@dataclass
class TranslateResult:
    yaml_text: str                            # 翻译后的 YAML 文本
    warnings: list[TranslateWarning] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(w.level == WarnLevel.ERROR for w in self.warnings)


# ── Archon 特殊变量 → $WI.* 映射 ─────────────────────────────────────────────

# (Archon 变量名, 本地 WI key, WI 类型)
_WI_VAR_MAP: list[tuple[str, str, str]] = [
    ('ARGUMENTS',    'arguments',    'str'),
    ('USER_MESSAGE', 'user_message', 'str'),
    ('BASE_BRANCH',  'base_branch',  'str'),
]

# 需要剥离(无对应本地变量)的 Archon 内置变量
_STRIP_VARS = {'WORKFLOW_ID', 'ARTIFACTS_DIR', 'LOOP_PREV_OUTPUT', 'LOOP_USER_INPUT',
               'REJECTION_REASON', 'DOCS_DIR'}

# ── 模型名映射 ────────────────────────────────────────────────────────────────

def _map_model(archon_model: str | None, provider: str | None) -> str | None:
    """把 Archon 模型/provider 映射到本地模型别名,None 表示用系统默认."""
    if not archon_model and not provider:
        return None
    combined = f'{provider or ""}/{archon_model or ""}'.lower()
    # Claude/Anthropic 家族 → 'claude'
    if any(x in combined for x in ('claude', 'anthropic', 'haiku', 'sonnet', 'opus')):
        return 'claude'
    # Qwen 家族 → 'qwen'
    if any(x in combined for x in ('qwen', 'dashscope', 'alibaba')):
        return 'qwen'
    # Doubao / Ark → 'doubao'
    if any(x in combined for x in ('doubao', 'ark', 'volce')):
        return 'doubao'
    # 其他(codex/pi/opencode/minimax)→ 默认 claude,但给警告
    return 'claude'


# ── 变量替换 ──────────────────────────────────────────────────────────────────

# 匹配 $VAR_NAME(不含 .output 结构,是纯 Archon 全局变量)
_GLOBAL_VAR_RE = re.compile(r'\$([A-Z][A-Z0-9_]*)\b')


def _replace_vars(text: str, wi_declared: set[str]) -> tuple[str, list[str]]:
    """
    把字符串中的 Archon 全局变量替换为 $WI.* 或剥离.
    返回 (替换后文本, [产生的 warning 消息]).
    """
    warnings: list[str] = []
    wi_map = {k: v for k, v, _ in _WI_VAR_MAP}

    def _sub(m: re.Match) -> str:
        var = m.group(1)
        if var in wi_map:
            wi_declared.add(var)
            return f'$WI.{wi_map[var]}'
        if var in _STRIP_VARS:
            warnings.append(f'变量 ${var} 无本地对应,已剥离')
            return f'[${var}]'  # 保留原文作为可读占位
        return m.group(0)  # 未知变量保持原样

    return _GLOBAL_VAR_RE.sub(_sub, text), warnings


def _replace_vars_deep(obj: Any, wi_declared: set[str]) -> tuple[Any, list[str]]:
    """递归替换字典/列表/字符串中的全局变量."""
    warnings: list[str] = []
    if isinstance(obj, str):
        new, w = _replace_vars(obj, wi_declared)
        return new, w
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            new_v, w = _replace_vars_deep(v, wi_declared)
            result[k] = new_v
            warnings.extend(w)
        return result, warnings
    if isinstance(obj, list):
        result_list = []
        for item in obj:
            new_item, w = _replace_vars_deep(item, wi_declared)
            result_list.append(new_item)
            warnings.extend(w)
        return result_list, warnings
    return obj, warnings


# ── 节点翻译 ──────────────────────────────────────────────────────────────────

_ARCHON_IGNORED_KEYS = {
    'output_format', 'allowed_tools', 'denied_tools', 'effort', 'thinking',
    'idle_timeout', 'context', 'hooks', 'mcp', 'skills', 'agents',
    'betas', 'sandbox', 'fallback_model', 'system_prompt', 'persist_session',
}


def _is_named_script(value: str) -> bool:
    """判断 script: 的值是命名脚本(简单标识符)还是内联代码."""
    return bool(re.match(r'^[a-zA-Z0-9_-]+$', value.strip()))


def _translate_node(
    raw: dict,
    workflow_provider: str | None,
    workflow_model: str | None,
    command_dir: Path | None,
    wi_declared: set[str],
) -> tuple[dict, list[TranslateWarning]]:
    """翻译单个 Archon 节点为本地 DagNodeSpec 字典."""
    warnings: list[TranslateWarning] = []
    node_id: str = raw.get('id', '?')

    def warn(level: str, msg: str) -> None:
        warnings.append(TranslateWarning(node_id, level, msg))

    # ── 通用字段 ──────────────────────────────────────────────────────────────
    out: dict[str, Any] = {'id': node_id}

    if 'depends_on' in raw:
        out['depends_on'] = raw['depends_on']
    if 'trigger_rule' in raw:
        out['trigger_rule'] = raw['trigger_rule']
    if 'when' in raw:
        when, w = _replace_vars(raw['when'], wi_declared)
        out['when'] = when
        for msg in w:
            warn(WarnLevel.WARN, msg)
    if 'retry' in raw:
        out['retry'] = raw['retry']

    # 忽略 Archon 专属字段(不警告,纯静默丢弃)
    for key in _ARCHON_IGNORED_KEYS:
        if key in raw:
            warn(WarnLevel.INFO, f'字段 {key!r} 已忽略(本地不支持)')

    # ── 节点类型检测 ──────────────────────────────────────────────────────────
    node_model = _map_model(raw.get('model', workflow_model),
                            raw.get('provider', workflow_provider))
    inputs: dict[str, Any] = {}

    # ── prompt ────────────────────────────────────────────────────────────────
    if 'prompt' in raw:
        out['routine'] = 'run_llm'
        prompt, pw = _replace_vars(raw['prompt'], wi_declared)
        inputs['prompt'] = prompt
        for msg in pw:
            warn(WarnLevel.WARN, msg)
        if node_model:
            inputs['model'] = node_model
        if 'system' in raw:
            system, sw = _replace_vars(raw['system'], wi_declared)
            inputs['system'] = system
            for msg in sw:
                warn(WarnLevel.WARN, msg)

    # ── command ───────────────────────────────────────────────────────────────
    elif 'command' in raw:
        out['routine'] = 'run_llm'
        cmd_name: str = raw['command']
        system_content = _load_command_file(cmd_name, command_dir)
        if system_content:
            inputs['system'] = system_content
        else:
            inputs['system'] = f'# TODO: 粘贴命令文件 {cmd_name!r} 内容'
            warn(WarnLevel.WARN, f'command {cmd_name!r} 文件未找到,system 留占位符')
        inputs['prompt'] = '$WI.arguments'
        wi_declared.add('ARGUMENTS')
        if node_model:
            inputs['model'] = node_model

    # ── bash ──────────────────────────────────────────────────────────────────
    elif 'bash' in raw:
        out['routine'] = 'run_bash'
        cmd, cw = _replace_vars(raw['bash'], wi_declared)
        inputs['cmd'] = cmd
        for msg in cw:
            warn(WarnLevel.WARN, msg)
        if 'timeout' in raw:
            inputs['timeout'] = raw['timeout'] // 1000  # ms → s

    # ── script ────────────────────────────────────────────────────────────────
    elif 'script' in raw:
        out['routine'] = 'run_script'
        runtime = raw.get('runtime', 'python')
        inputs['runtime'] = runtime
        script_val: str = str(raw['script'])
        if _is_named_script(script_val):
            ext = '.ts' if runtime == 'bun' else '.py'
            inputs['file'] = f'.archon/scripts/{script_val}{ext}'
            warn(WarnLevel.INFO, f'命名脚本 {script_val!r} → file: {inputs["file"]},请确认路径')
        else:
            code, cw = _replace_vars(script_val, wi_declared)
            inputs['code'] = code
            for msg in cw:
                warn(WarnLevel.WARN, msg)
        if 'timeout' in raw:
            inputs['timeout'] = raw['timeout'] // 1000

    # ── loop ──────────────────────────────────────────────────────────────────
    elif 'loop' in raw:
        loop_cfg: dict = raw['loop']
        out['routine'] = 'run_llm'
        prompt, pw = _replace_vars(loop_cfg.get('prompt', ''), wi_declared)
        inputs['prompt'] = prompt
        for msg in pw:
            warn(WarnLevel.WARN, msg)
        if node_model:
            inputs['model'] = node_model
        warn(WarnLevel.WARN,
             f'loop 节点近似为单轮 run_llm(until={loop_cfg.get("until")!r} '
             f'max_iterations={loop_cfg.get("max_iterations")} 均已丢弃).'
             '若需真正迭代,请改用 agent routine.')

    # ── approval ──────────────────────────────────────────────────────────────
    elif 'approval' in raw:
        out['routine'] = 'dag_approval'
        approval_cfg: dict = raw.get('approval') or {}
        if isinstance(approval_cfg, dict):
            if 'message' in approval_cfg:
                msg_text, mw = _replace_vars(approval_cfg['message'], wi_declared)
                inputs['message'] = msg_text
                for m in mw:
                    warn(WarnLevel.WARN, m)
            if 'capture_response' in approval_cfg:
                inputs['capture_response'] = approval_cfg['capture_response']
            if 'max_attempts' in approval_cfg:
                inputs['max_attempts'] = approval_cfg['max_attempts']
        else:
            inputs['message'] = '请审批'

    # ── 未知节点类型 ──────────────────────────────────────────────────────────
    else:
        known_type_keys = {'prompt', 'command', 'bash', 'script', 'loop', 'approval'}
        unknown = [k for k in raw if k not in known_type_keys | {'id', 'depends_on',
                   'trigger_rule', 'when', 'retry', 'model', 'provider', 'system'}
                   | _ARCHON_IGNORED_KEYS]
        out['routine'] = 'echo'
        inputs['_raw'] = str(raw)
        warn(WarnLevel.ERROR, f'无法识别节点类型(未知字段: {unknown}),已替换为 echo 占位符')

    if inputs:
        out['inputs'] = inputs

    return out, warnings


def _load_command_file(name: str, command_dir: Path | None) -> str | None:
    """尝试从 command_dir 加载命令文件内容."""
    if not command_dir:
        return None
    for ext in ('.md', '.txt', ''):
        path = command_dir / f'{name}{ext}'
        if path.exists():
            return path.read_text(encoding='utf-8').strip()
    return None


# ── 主翻译函数 ────────────────────────────────────────────────────────────────

def translate(
    archon_yaml: str,
    command_dir: str | Path | None = None,
) -> TranslateResult:
    """将 Archon 工作流 YAML 文本翻译为本地 DAG YAML.

    Parameters
    ----------
    archon_yaml:
        Archon 格式的 YAML 文本.
    command_dir:
        `.archon/commands/` 目录路径,用于解析 `command:` 节点的命令文件内容.
        为 None 时,command 节点的 system 留占位符.

    Returns
    -------
    TranslateResult
        包含翻译后 YAML 文本和警告列表.
    """
    cmd_dir = Path(command_dir) if command_dir else None
    raw_doc: dict = yaml.safe_load(archon_yaml) or {}

    all_warnings: list[TranslateWarning] = []
    wi_declared: set[str] = set()   # 收集用到的 WI 变量

    wf_provider: str | None = raw_doc.get('provider')
    wf_model: str | None    = raw_doc.get('model')

    # 翻译节点
    translated_nodes: list[dict] = []
    for raw_node in raw_doc.get('nodes', []):
        node_out, node_warns = _translate_node(
            raw_node, wf_provider, wf_model, cmd_dir, wi_declared,
        )
        translated_nodes.append(node_out)
        all_warnings.extend(node_warns)

    # 构建 DAG inputs(根据用到的 WI 变量自动声明)
    wi_type_map = {k: t for k, _, t in _WI_VAR_MAP}
    wi_name_map = {k: v for k, v, _ in _WI_VAR_MAP}
    dag_inputs: list[dict] = []
    for archon_var in sorted(wi_declared):
        if archon_var in wi_name_map:
            dag_inputs.append({
                'name': wi_name_map[archon_var],
                'type': wi_type_map.get(archon_var, 'str'),
                'default': '',
            })

    # 处理 outputs
    dag_outputs = raw_doc.get('outputs')

    # 组装最终 DAG 文档
    dag_doc: dict[str, Any] = {
        'name': raw_doc.get('name', 'translated_workflow'),
        'description': raw_doc.get('description', '').strip(),
    }
    if dag_inputs:
        dag_doc['inputs'] = dag_inputs
    dag_doc['nodes'] = translated_nodes
    if dag_outputs:
        dag_doc['outputs'] = dag_outputs

    # 生成 YAML(保留多行字符串可读性)
    yaml_text = _dump_yaml(dag_doc, archon_yaml)

    return TranslateResult(yaml_text=yaml_text, warnings=all_warnings)


# ── YAML 序列化 ───────────────────────────────────────────────────────────────

class _LiteralStr(str):
    """标记需要用 | 块风格输出的字符串."""


def _literal_representer(dumper: yaml.Dumper, data: _LiteralStr):
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')


def _str_representer(dumper: yaml.Dumper, data: str):
    if '\n' in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


def _make_dumper() -> type:
    class _Dumper(yaml.Dumper):
        pass
    _Dumper.add_representer(str, _str_representer)
    _Dumper.add_representer(_LiteralStr, _literal_representer)
    _Dumper.ignore_aliases = lambda *_: True
    return _Dumper


def _dump_yaml(doc: dict, source_yaml: str) -> str:
    """生成带来源注释头部的 YAML 文本."""
    source_name = ''
    try:
        raw = yaml.safe_load(source_yaml) or {}
        source_name = raw.get('name', '')
    except Exception:
        pass

    header = textwrap.dedent(f'''\
        # 本文件由 archon_translator 自动生成
        # 来源工作流: {source_name}
        # 请检查所有 # TODO 注释并补全缺失内容
        #
    ''')

    body = yaml.dump(doc, Dumper=_make_dumper(), allow_unicode=True,
                     default_flow_style=False, sort_keys=False, width=120)
    return header + body


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse, sys

    parser = argparse.ArgumentParser(
        description='将 Archon 工作流 YAML 翻译为本地 DAG YAML',
    )
    parser.add_argument('input', help='Archon YAML 文件路径')
    parser.add_argument('-o', '--output', help='输出文件路径(默认 stdout)')
    parser.add_argument('--commands', help='.archon/commands/ 目录路径')
    args = parser.parse_args()

    src = Path(args.input).read_text(encoding='utf-8')
    result = translate(src, command_dir=args.commands)

    if args.output:
        Path(args.output).write_text(result.yaml_text, encoding='utf-8')
        print(f'写出 → {args.output}')
    else:
        print(result.yaml_text)

    if result.warnings:
        print('\n─── 翻译警告 ───', file=sys.stderr)
        for w in result.warnings:
            print(f'  {w}', file=sys.stderr)

    if result.has_errors:
        sys.exit(1)


if __name__ == '__main__':
    main()
