"""将 Archon 工作流 YAML 翻译为本地 DAG YAML 并保存到 dags/archon/ 的 routine."""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine
from routine.logger import setup_logger

from .translator import translate

logger = setup_logger('dag.archon_translator')

# 输出目录:dags/archon/
_ARCHON_OUT_DIR: Path = Path(__file__).parents[1] / 'dags' / 'archon'


class TranslateArchonInput(BaseModel):
    path: str = Field(description='Archon YAML 文件路径(绝对路径或相对当前工作目录)')
    commands_dir: str = Field(
        default='',
        description='.archon/commands/ 目录路径,用于解析 command 节点(可选)',
    )
    out_name: str = Field(default='', description='输出文件名(可选,默认与输入同名)')


class TranslateArchonOutput(BaseModel):
    output: str = Field(description='生成的本地 DAG YAML 文件绝对路径')
    warnings: list = Field(default_factory=list, description='翻译过程中的警告列表')
    has_errors: bool = Field(description='是否含有错误级别的问题')


class TranslateArchon(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '把 Archon 工作流 YAML 翻译为本地 DAG YAML,保存到 dags/archon/.',
        'input_schema': TranslateArchonInput.model_json_schema(),
        'output_schema': TranslateArchonOutput.model_json_schema(),
    }
    """把 Archon 工作流 YAML 翻译为本地 DAG YAML,保存到 dags/archon/.

    inputs
    ------
    path         Archon YAML 文件路径(绝对路径或相对当前工作目录)
    commands_dir .archon/commands/ 目录路径,用于解析 command 节点(可选)
    out_name     输出文件名(可选,默认与输入同名)
    """

    async def run(
        self,
        kwargs: Dict[str, Any],
    ) -> dict:
        path = kwargs['path']
        commands_dir = kwargs.get('commands_dir', '')
        out_name = kwargs.get('out_name', '')
        src_path = Path(path).expanduser().resolve()
        if not src_path.exists():
            raise FileNotFoundError(f'Archon YAML 不存在: {src_path}')

        src_text = src_path.read_text(encoding='utf-8')

        cmd_dir = Path(commands_dir).expanduser().resolve() if commands_dir else None
        result = translate(src_text, command_dir=cmd_dir)

        # 确保输出目录存在
        _ARCHON_OUT_DIR.mkdir(parents=True, exist_ok=True)

        filename = out_name or src_path.name
        if not filename.endswith('.yaml'):
            filename += '.yaml'
        out_path = _ARCHON_OUT_DIR / filename

        out_path.write_text(result.yaml_text, encoding='utf-8')
        logger.info('translated %s -> %s (%d warnings)',
                    src_path.name, out_path, len(result.warnings))

        warnings_summary = [str(w) for w in result.warnings]
        return {
            'output': str(out_path),
            'warnings': warnings_summary,
            'has_errors': result.has_errors,
        }
