"""IPython - persistent per-session Python execution backed by a real ipykernel."""
from __future__ import annotations

import asyncio
import atexit
import os
import re
from pathlib import Path
from typing import Any, ClassVar, Dict

from jupyter_client import AsyncKernelManager
from pydantic import BaseModel, Field
from routine import Routine
from routine.logger import setup_logger

from app.routines.user.agents._core.paths import AGENT_ID_KEY, resolve_optional_tool_path
from app.routines.user.skill.registry import USER_SKILLS_DIR
from app.routines.user.agents.prime.kernel_env import ensure_kernel_env
from .prompt import DESCRIPTION

_log = setup_logger('ipython')

_DEFAULT_TIMEOUT_SECONDS = 120
_MAX_TIMEOUT_SECONDS = 120

# ipykernel 的 traceback 带 ANSI 颜色码, 剥掉得到纯文本
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

# skill 库里 routine-bridge 的源码路径 (skills/routine-bridge/src/routine_bridge/...).
# 用绝对路径显式加载, 绕开 sys.path 上同名 routine 包的遮蔽.
# 包名用 routine_bridge 避免跟 routine SDK 顶层包重名 (sys.modules 冲突).
_SKILL_ROUTINE_INIT = (
    USER_SKILLS_DIR / 'routine-bridge' / 'src' / 'routine_bridge' / '__init__.py'
)

# Bootstrap code: 显式路径加载 skill 包, 绕开 sys.path 顺序问题.
# 失败时不静默吞掉, 在 user_ns 留错误痕迹让 agent 能感知.
# 同时把 agent_id 写入 kernel 进程环境变量, 让 run_routine skill 能读到
# 并作为 HTTP header 传给 bridge, bridge 再注入到 routine 的 from_agent_id.
def _build_bootstrap_code(agent_id: str) -> str:
    safe_id = agent_id.replace("'", "\\'")
    # kernel 地址从 zero 进程 env 透传到 IPython 子进程, hub_routine 等
    # skill 经 start_hub() 自动读到, agent 无需关心地址. 默认与 app/main.py
    # 的 grpc server 端口一致(ZERO_KERNEL_ADDR 是事实源, 这里只是 fallback).
    kernel_addr = os.environ.get('ZERO_KERNEL_ADDR', '127.0.0.1:7795')
    # bridge 地址同样透传: web_server 启动时把实际绑定地址写入
    # ZERO_HTTP_ADDR(事实源, 端口由 kwargs 决定), routine_bridge 读它回拨.
    http_addr = os.environ.get('ZERO_HTTP_ADDR', '127.0.0.1:7781')
    return f"""\
try:
    import os as _os
    _os.environ['ZERO_AGENT_ID'] = '{safe_id}'
    _os.environ['ZERO_KERNEL_ADDR'] = '{kernel_addr}'
    _os.environ['ZERO_HTTP_ADDR'] = '{http_addr}'
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location('routine_bridge', r'{_SKILL_ROUTINE_INIT}')
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    run_routine = _mod.run_routine
except Exception as _e:
    run_routine = None
    __run_routine_error__ = repr(_e)
"""


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)


# display_data 图像捕获 (prime attach 机制): display 的 image/* 不进 stdout,
# 单独收集为附件, 由调用方 (agent 引擎) 拼进 tool result 的 image block.
_IMAGE_MIMES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp')
_MAX_IMAGE_B64_CHARS = 500_000   # 单张 base64 上限 (约 375KB 原始字节)
_MAX_IMAGES_PER_EXEC = 8


def _format_notebook_output(response: dict[str, Any]) -> dict[str, Any]:
    """把 kernel 响应格式化为 LLM-friendly 文本.

    Returns:
        {'content': str, 'displays': [...]}
        - content: 纯文本 (stdout/stderr/result/error), 给 LLM 看.
        - displays: display_data 捕获的图像附件 [{mime, data}], 由 agent
          引擎拼进 tool result 的 image block (模型视觉输入).
    """
    error = str(response.get('error') or '')
    if error:
        return {'content': error.rstrip(), 'displays': response.get('displays') or []}

    parts: list[str] = []
    stdout = str(response.get('stdout') or '')
    stderr = str(response.get('stderr') or '')
    result = response.get('result')

    if stdout:
        parts.append(stdout.rstrip())
    if stderr:
        parts.append(stderr.rstrip())
    if result not in (None, '', 'None'):
        parts.append(str(result))

    return {
        'content': '\n'.join(p for p in parts if p),
        'displays': response.get('displays') or [],
    }


class IPythonInput(BaseModel):
    code: str = Field(description='Python code to execute in the persistent IPython kernel.')
    timeout: int = Field(
        _DEFAULT_TIMEOUT_SECONDS,
        description='Timeout in seconds. Default 30, max 120.',
    )
    reset: bool = Field(
        False,
        description='Clear the IPython user namespace before executing this code. Preserves the kernel process.',
    )


class _Display(BaseModel):
    mime: str = Field(description='Image MIME type (image/png, ...)')
    data: str = Field(description='Base64-encoded image bytes')


class IPythonOutput(BaseModel):
    content: str = Field(
        description='Notebook-like cell output: stdout/stderr followed by the final expression repr, or traceback on error.',
    )
    displays: list[_Display] = Field(
        default_factory=list,
        description='Images captured from display_data (e.g. plt.show()); attach as image blocks in the tool result.',
    )


class IPython(Routine):
    """Run Python snippets in a persistent per-session ipykernel."""
    name = 'ipython'

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': False,
        'input_schema': IPythonInput.model_json_schema(),
        'output_schema': IPythonOutput.model_json_schema(),
        'description': DESCRIPTION,
    }

    def __init__(self) -> None:
        super().__init__()
        self._active_worker: '_ConsoleWorker | None' = None

    async def run(self, kwargs: Dict[str, Any]) -> str:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        if agent_id:
            state = await self.call('agent/state', {'agent_id': agent_id})
            session_id = state.get('session_id') or ''
            project_root = state.get('project_root')
        else:
            # 无 agent 上下文(前端 HTTP 直调): 用固定 session, 方便测试.
            session_id = '__adhoc__'
            project_root = None
        if not session_id:
            raise RuntimeError('IPython requires an active session_id.')
        inp = IPythonInput(**kwargs)
        timeout = max(1, min(inp.timeout, _MAX_TIMEOUT_SECONDS))

        worker = await _ConsoleRegistry.get(session_id, project_root, agent_id)
        self._active_worker = worker
        response, kernel_alive = await worker.execute(
            code=inp.code, reset=inp.reset, timeout=timeout,
        )
        # interrupt 失败 (drain 没收到 idle) → kill 重启.
        if not kernel_alive:
            await _ConsoleRegistry.kill(session_id)
            raise TimeoutError(f'IPython timed out after {timeout}s and interrupt failed')

        return IPythonOutput(**_format_notebook_output(response)).model_dump()

    async def stop(self) -> None:
        """打断钩子: 用户 steering/interrupt 触发 epoch 切换 → routine 被 stop.
        此时 run() 卡在 _execute_cell 的 iopub 轮询 (cancel 不传播到 zmq),
        kernel 里的 cell 还在跑. interrupt_kernel 让它在执行点抛
        KeyboardInterrupt, kernel 回到 idle, 进程和变量保留, 下次可复用."""
        worker = self._active_worker
        if worker is None or worker.km is None:
            return
        try:
            await worker.km.interrupt_kernel()
        except Exception as exc:
            _log.warning('stop hook interrupt_kernel failed: %r', exc)


class _ConsoleWorker:
    """Holds one AsyncKernelManager + AsyncKernelClient per session."""

    def __init__(self, *, cwd: str, agent_id: str = '') -> None:
        self.cwd = cwd
        self.agent_id = agent_id
        self.km: AsyncKernelManager | None = None
        self.kc: Any = None
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        if self.km is not None and await self._is_alive():
            return
        # 清理上一轮残留
        if self.kc is not None:
            try:
                self.kc.stop_channels()
            except Exception:
                pass
            self.kc = None
        if self.km is not None:
            try:
                await self.km.shutdown_kernel(now=True)
            except Exception:
                pass
            self.km = None

        # 确保 kernel venv 就绪 (创建 venv + 装好 Python-backed skill).
        # 用 venv python 启动 kernel, skill 包直接 import, 不走 sys.path hack.
        # ensure_kernel_env 内部有 subprocess.run (uv venv / uv pip install),
        # 同步调用会阻塞 event loop → HTTP/WebSocket 卡死 + wait_for timeout 失效.
        # 用 asyncio.to_thread 扔到线程池, 不阻塞 event loop.
        venv_python = await asyncio.to_thread(ensure_kernel_env, USER_SKILLS_DIR)

        km = AsyncKernelManager(kernel_name='python3')
        # 覆盖 kernel spec argv[0] 为 venv python.
        spec = km.kernel_spec
        if spec and spec.argv:
            spec.argv[0] = venv_python
        await km.start_kernel(cwd=self.cwd)
        kc = km.client()
        kc.start_channels()
        await kc.wait_for_ready(timeout=30.0)
        self.km = km
        self.kc = kc

        # 注入 run_routine 到 kernel user namespace.
        # skill 包已 pip install 进 venv, 直接 import.
        try:
            await self._execute_cell(_build_bootstrap_code(self.agent_id), silent=True)
        except Exception as exc:
            _log.warning('bootstrap import failed: %r', exc)

    async def _is_alive(self) -> bool:
        if self.km is None:
            return False
        try:
            return bool(await self.km.is_alive())
        except Exception:
            return False

    async def execute(
        self, *, code: str, reset: bool, timeout: float,
    ) -> tuple[dict[str, Any], bool]:
        """执行 code, 超时先 interrupt 保住 kernel 状态, 再不行才抛.

        Returns:
            (response, kernel_alive): kernel_alive=False 表示 interrupt 失败,
            调用方应 kill 重启.
        """
        async with self.lock:
            await self.start()
            assert self.kc is not None

            # 用户在 code 里直接写 %reset -f 也算 reset (LLM 常这么干, 不知道有 reset 字段).
            # 剥掉 reset 魔法行, 避免重复执行.
            if '%reset' in code:
                stripped_lines = []
                for line in code.splitlines():
                    if not line.strip().startswith('%reset'):
                        stripped_lines.append(line)
                code = '\n'.join(stripped_lines).strip()
                reset = True

            if reset:
                # IPython 魔法，清空 user namespace；保留 kernel 进程和已加载扩展。
                # %reset -f 会清掉 start() 注入的 run_routine, 需要重新注入.
                await self._execute_cell('%reset -f', silent=True)
                try:
                    await self._execute_cell(_build_bootstrap_code(self.agent_id), silent=True)
                except Exception as exc:
                    _log.warning('re-bootstrap after reset failed: %r', exc)

            # reset 后 code 可能为空 (用户只写了 %reset -f), 不再执行.
            if not code:
                return {'stdout': 'namespace reset, run_routine re-injected',
                        'stderr': '', 'result': None, 'error': None}, True

            try:
                response = await self._execute_cell(code, silent=False, timeout=timeout)
                return response, True
            except asyncio.TimeoutError:
                # 超时: _execute_cell 内部 deadline 到了, 主动退出循环.
                # kernel 那边代码还在跑, interrupt_kernel 让它在执行点抛 KeyboardInterrupt,
                # drain iopub 收尾, 确认 kernel 回到 idle. 整个在 lock 内,
                # 下一个 execute 会排队等, 不会撞上残留的 execute_request.
                ok = await self.interrupt_and_drain(timeout=5.0)
                if not ok:
                    return {}, False
                return {
                    'stdout': '',
                    'stderr': '',
                    'result': None,
                    'error': f'执行超时 ({timeout}s), 已中断. kernel 状态保留, 变量未丢失.',
                    'displays': [],
                }, True

    async def interrupt_and_drain(self, *, timeout: float = 5.0) -> bool:
        """超时收尾: 发 interrupt + drain iopub 到 idle. 必须在 lock 内调用.

        kernel 那边代码还在跑, interrupt_kernel 发 control channel 消息,
        kernel 在执行点抛 KeyboardInterrupt, 随后发 error + idle iopub 消息.
        drain 掉这些消息, 确认 kernel 回到空闲, 不让残留消息污染下一次 execute.

        Returns:
            True = 收到 idle, kernel 已回到空闲, 状态保留.
            False = drain 超时没收到 idle, kernel 可能卡死, 调用方应 kill.
        """
        if self.km is None:
            return False
        try:
            await self.km.interrupt_kernel()
        except Exception as exc:
            _log.warning('interrupt_kernel failed: %r', exc)
            return False
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return False
            try:
                msg = await asyncio.wait_for(self.kc.get_iopub_msg(), timeout=remaining)
            except asyncio.TimeoutError:
                return False
            msg_type = msg.get('header', {}).get('msg_type')
            content = msg.get('content') or {}
            if msg_type == 'status' and content.get('execution_state') == 'idle':
                return True

    async def _execute_cell(self, code: str, *, silent: bool, timeout: float = 30.0) -> dict[str, Any]:
        assert self.kc is not None
        # AsyncKernelClient.execute / start_channels 不是 coroutine, 直接调用。
        msg_id = self.kc.execute(
            code,
            silent=silent,
            store_history=not silent,
            user_expressions={},
            allow_stdin=False,
        )

        stdout = ''
        stderr = ''
        result: Any = None
        has_result = False
        error: str | None = None
        displays: list[dict[str, str]] = []
        # 内部 deadline: 不依赖外层 wait_for cancel (Windows + zmq + ProactorEventLoop
        # 下 cancel 不传播), 自己检查 deadline 到了直接抛 TimeoutError.
        deadline = asyncio.get_event_loop().time() + timeout

        while True:
            if asyncio.get_event_loop().time() > deadline:
                raise asyncio.TimeoutError()
            remaining = deadline - asyncio.get_event_loop().time()
            wait = min(1.0, max(0.1, remaining))
            try:
                msg = await asyncio.wait_for(self.kc.get_iopub_msg(), timeout=wait)
            except asyncio.TimeoutError:
                if not await self._is_alive():
                    raise RuntimeError('ipykernel exited unexpectedly while executing code.')
                continue

            parent = msg.get('parent_header') or {}
            if parent.get('msg_id') != msg_id:
                continue

            msg_type = msg.get('header', {}).get('msg_type')
            content = msg.get('content') or {}

            if msg_type == 'stream':
                name = content.get('name', 'stdout')
                text = content.get('text', '')
                if name == 'stderr':
                    stderr += text
                else:
                    stdout += text
            elif msg_type == 'execute_result':
                data = content.get('data') or {}
                text_plain = data.get('text/plain')
                if text_plain is not None:
                    result = text_plain
                    has_result = True
            elif msg_type == 'display_data':
                data = content.get('data') or {}
                # 图像附件捕获 (prime attach 机制): display 的 image/* 单独
                # 收集, 与 text/plain 兜底互不干扰
                for mime in _IMAGE_MIMES:
                    b64 = data.get(mime)
                    if isinstance(b64, str) and b64:
                        if len(b64) > _MAX_IMAGE_B64_CHARS:
                            stderr += (f'\n[image dropped: {mime} exceeds '
                                       f'{_MAX_IMAGE_B64_CHARS} base64 chars]')
                        elif len(displays) >= _MAX_IMAGES_PER_EXEC:
                            stderr += (f'\n[image dropped: more than '
                                       f'{_MAX_IMAGES_PER_EXEC} images per execution]')
                        else:
                            displays.append({'mime': mime, 'data': b64})
                        break
                text_plain = data.get('text/plain')
                if text_plain is not None and not has_result:
                    result = text_plain
                    has_result = True
            elif msg_type == 'error':
                traceback = content.get('traceback') or []
                if traceback:
                    error = _strip_ansi('\n'.join(traceback).rstrip())
                else:
                    error = f"{content.get('ename', 'Error')}: {content.get('evalue', '')}"
            elif msg_type == 'status' and content.get('execution_state') == 'idle':
                break

        # 同步取 shell 上的 execute_reply（拿 status: ok/error/aborted）
        reply_status = 'ok'
        try:
            reply = await asyncio.wait_for(self.kc.get_shell_msg(), timeout=1.0)
            reply_status = (reply.get('content') or {}).get('status', 'ok')
        except asyncio.TimeoutError:
            pass

        if error is None and reply_status == 'aborted':
            error = 'Execution aborted.'

        return {
            'stdout': stdout,
            'stderr': stderr,
            'result': result if has_result else None,
            'error': error,
            'displays': displays,
        }

    async def stop(self) -> None:
        km, kc = self.km, self.kc
        self.km, self.kc = None, None
        if kc is not None:
            try:
                kc.stop_channels()
            except Exception:
                pass
        if km is not None:
            try:
                await km.shutdown_kernel(now=False)
            except Exception:
                try:
                    km.kill_kernel()
                except Exception:
                    pass

    def kill_now(self) -> None:
        """同步兜底，atexit / 信号 handler 调用，不能 await。"""
        km, kc = self.km, self.kc
        self.km = None
        self.kc = None
        # 先关 channels 再杀进程: 不关的话 GC 销毁 zmq context 失败,
        # traitlets 打 "Could not destroy zmq context" 警告.
        if kc is not None:
            try:
                kc.stop_channels()
            except Exception:
                pass
        if km is not None:
            try:
                km.kill_kernel()
            except Exception:
                pass


class _ConsoleRegistry:
    _workers: ClassVar[dict[str, _ConsoleWorker]] = {}

    @classmethod
    async def get(cls, session_id: str, project_root: str | None,
                  agent_id: str = '') -> _ConsoleWorker:
        cwd = resolve_optional_tool_path(None, project_root)
        worker = cls._workers.get(session_id)
        if worker and (worker.cwd != cwd or worker.agent_id != agent_id):
            await worker.stop()
            worker = None
        if worker is None:
            worker = _ConsoleWorker(cwd=cwd, agent_id=agent_id)
            cls._workers[session_id] = worker
        return worker

    @classmethod
    async def kill(cls, session_id: str) -> None:
        worker = cls._workers.pop(session_id, None)
        if worker:
            await worker.stop()

    @classmethod
    def kill_all_now(cls) -> None:
        workers = list(cls._workers.values())
        cls._workers.clear()
        for worker in workers:
            worker.kill_now()


atexit.register(_ConsoleRegistry.kill_all_now)
