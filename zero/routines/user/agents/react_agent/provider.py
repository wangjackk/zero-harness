"""ReactContextProvider — react_agent 上下文存取抽象.

封装 Memory (sqlite 持久化) + 可选 OVMemory (OpenViking 长期记忆),
统一消息读写、持久化、OV 推送、长期记忆检索.

agent 只依赖此接口, 不直接接触 Memory / OVMemory.
Memory 同时承担 agents 表管理 (manager 用), 通过 memory 属性暴露.

本地 DB 是 source of truth: resume 还原 + 上下文压缩全本地化.
OV 只做同步备份 (tick + finalize) 和长期记忆查询 (find), 不参与 resume/压缩.
压缩走 react_condenser_agent (写 summary 到 messages 表, kind='summary').
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from routine.logger import setup_logger

from .memory import Memory, get_memory

_log = setup_logger('react_agent.provider')


class ReactContextProvider:
    """react_agent 上下文 provider: Memory 持久化 + OV 长期记忆.

    Memory: sqlite 持久化 (messages + agents 表), 重启保留, manager 共用.
    OVMemory: OpenViking 长期记忆推送 + 语义检索, 失败不阻断主流程.

    peer_id 默认 'react' (跟 prime 的 'claude' 隔离, 跨系不共享).
    """

    def __init__(
        self,
        *,
        memory: Memory | None = None,
        ov_config: dict[str, Any] | None = None,
        workspace: Path | None = None,
        peer_id: str = 'react',
        agent_id: str = '',
    ) -> None:
        self._mem = memory or get_memory()
        self._ov: Any = None
        self._ov_config = ov_config
        self._workspace = workspace
        self._peer_id = peer_id
        self._session_id: Optional[str] = None
        self._agent_id: str = agent_id

    # --- 属性 ---

    @property
    def enabled(self) -> bool:
        """OV 是否启用 (Memory 总是启用)."""
        return self._ov is not None and self._ov.enabled

    @property
    def ov(self) -> Any:
        """暴露底层 OVMemory 供文件操作 (None if disabled)."""
        if self._ov is not None and self._ov.enabled:
            return self._ov
        return None

    @property
    def memory(self) -> Memory:
        """暴露 Memory 供 manager 做 agents 表管理."""
        return self._mem

    # --- 生命周期 ---

    async def init_session(self, session_id: str) -> None:
        """初始化: 记录 session_id + 后台初始化 OV + 对齐游标.

        非阻塞: OV 建连接在后台 task 跑, 不卡 agent 启动.
        cursor 对齐在 init task 完成后自动执行.
        """
        self._session_id = session_id
        if not self._ov_config:
            return
        try:
            # 可选集成点: 应用提供 zero.routines._shared.ov_memory.OVMemory
            # 则启用长期记忆; 缺省 (模块不存在) 走 except -> OV 禁用, 不影响主流程.
            from zero.routines._shared.ov_memory import OVMemory
            self._ov = OVMemory(
                self._ov_config, self._workspace, peer_id=self._peer_id,
            )
            await self._ov.init_async(session_id)
            # cursor 对齐延迟到 init 完成后执行 (非阻塞)
            local_items = self._mem.load_history(session_id)
            asyncio.create_task(self._ov_align_after_init(local_items))
        except Exception as exc:
            _log.warning('ov init failed: %s (OV disabled)', exc)
            self._ov = None

    async def _ov_align_after_init(self, items: list[dict]) -> None:
        """等 OV init task 完成后从游标文件恢复 cursor.

        游标文件记录 OV 端真实最后推送的 msg_id, tick 据此补推未上传的增量.
        本地有数据时也走 load_cursor —— 不能假设"本地有的 OV 也有"(崩溃场景 tick 可能没推完).
        """
        if self._ov is None or self._ov._init_task is None:
            return
        try:
            await self._ov._init_task
        except Exception:
            return
        if not self._ov.enabled:
            return
        self._ov.load_cursor(items)

    async def finalize_session(self) -> None:
        """关闭: 推剩余消息 + commit 归档 + close (agent=session, 关闭即结束)."""
        if self._ov is None or not self._ov.enabled:
            return
        items = (
            self._mem.load_history(self._session_id)
            if self._session_id else None
        )
        try:
            await self._ov.finalize(items)
        except Exception as exc:
            _log.warning('ov finalize failed: %s', exc)

    # --- 写入 (Memory 持久化) ---

    def add_message(
        self,
        role: str,
        content: str,
        *,
        agent_id: str,
        message_id: str,
        session_id: str,
        interrupted: bool = False,
        feedback: Optional[list] = None,
        results_raw: Optional[list] = None,
        response_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        """追加消息到 Memory (后台写, 不阻塞)."""
        self._mem.add_message(
            role, content,
            agent_id=agent_id, message_id=message_id, session_id=session_id,
            interrupted=interrupted, feedback=feedback, results_raw=results_raw,
            response_id=response_id, model=model,
        )

    # --- 读取 ---

    def load_history(self, session_id: str) -> list[dict]:
        """LLM 输入格式 history (含 kind='summary' 行, agent 侧投影切)."""
        return self._mem.load_history(session_id)

    def load_messages(self, session_id: str) -> list[dict]:
        """前端展示格式 messages (跳过 [feedback] 和 summary)."""
        return self._mem.load_messages(session_id)

    # --- response_id 管理 (prompt caching) ---

    def get_last_response_id(
        self, session_id: str, *, model: str | None = None,
    ) -> str | None:
        return self._mem.get_last_response_id(session_id, model=model)

    def clear_last_response_id(
        self, session_id: str, response_id: str,
    ) -> None:
        self._mem.clear_last_response_id(session_id, response_id)

    # --- 持久化屏障 ---

    def flush(self) -> None:
        """阻塞等到所有入队写操作落盘."""
        self._mem.flush()

    # --- 长期记忆 (OV 语义检索) ---

    async def find(self, query: str, limit: int = 5) -> str:
        if not self.enabled:
            return 'OV 未启用, 无法语义检索'
        return await self._ov.find(query, limit=limit)

    # --- 增量推送 (OV, 每 N 轮 add_message, 不 commit) ---

    def tick(self) -> None:
        """每轮调用, 内部按 N 轮增量推 OV (不 commit, 留给 finalize)."""
        if not self.enabled or not self._session_id:
            return
        self._ov.tick_and_maybe_push(self._mem.load_history(self._session_id))

    # --- ContextProvider Protocol (统一接口) ---

    def append_user(self, text: str) -> None:
        """追加 user 消息 (Protocol)."""
        self._mem.add_message(
            'user', text,
            agent_id=self._agent_id,
            message_id=uuid4().hex, session_id=self._session_id or '',
        )

    def append_assistant(self, text: str) -> None:
        """追加 assistant 消息 (Protocol, 空文本跳过)."""
        if not text:
            return
        self._mem.add_message(
            'assistant', text,
            agent_id=self._agent_id,
            message_id=uuid4().hex, session_id=self._session_id or '',
        )

    def append_function_call(
        self, name: str, arguments: str, call_id: str,
    ) -> None:
        """追加 function_call (Protocol; react_agent 用 act 不走 function call → no-op)."""
        pass

    def append_function_output(
        self, call_id: str, output: str,
        raw_result: Any | None = None,
    ) -> None:
        """追加 function_call_output (Protocol; react_agent → no-op)."""
        pass

    def items(self) -> list[dict]:
        """当前消息列表 (Protocol, 从 Memory 读)."""
        if not self._session_id:
            return []
        return self._mem.load_history(self._session_id)

    def __len__(self) -> int:
        return len(self.items())

    # --- 压缩 (本地 react_condenser_agent, 与 OV 无关) ---

    async def compact(
        self,
        *,
        agent_id: str,
        session_id: str,
        model_key: str,
        max_context: int,
        plan_mode: bool,
        condense_config: dict[str, Any],
        project_root: str | None,
        cwd: str | None,
        call: Any,
    ) -> dict[str, Any] | None:
        """本地压缩: 走 react_condenser_agent (写 summary 到 messages 表).

        压缩完全本地化, 与 OV 无关. OV 只做同步备份 + 长期记忆查询.
        实现 ContextProvider Protocol 签名 (model_key/plan_mode/project_root/cwd/call
        被 react_agent 忽略, 保留签名兼容).

        返回 {'compacted': True} 表示压缩了, None 表示未压缩.
        """
        if not self._session_id:
            return None
        items = self._mem.load_history(session_id)
        if len(items) < 2:
            return None

        # 估算 token (粗略 4 chars/token)
        rough_tokens = sum(
            len(str(i.get('content', ''))) for i in items
        ) // 4
        trigger_ratio = condense_config.get('trigger_ratio', 0.8)
        if max_context <= 0 or rough_tokens < max_context * trigger_ratio:
            return None

        _log.info(
            'compact: session=%s items=%d rough_tokens=%d budget=%d threshold=%d',
            session_id, len(items), rough_tokens, max_context,
            int(max_context * trigger_ratio),
        )

        # 调本地 react_condenser_agent (走 wire, 跟 prime condenser_agent 平级)
        try:
            result = await call('react_condenser_agent', {
                'agent_id': agent_id,
                'session_id': session_id,
                'model_key': model_key,
                'strategy': 'hybrid',
                'config': condense_config,
            })
        except Exception as exc:
            _log.warning('react_condenser call failed: %s (skipping)', exc)
            return None

        if not isinstance(result, dict) or not result.get('condensed'):
            return None

        _log.info(
            'context condensed: %d -> %d tokens (strategy=%s)',
            result.get('tokens_before', 0),
            result.get('tokens_after', 0),
            result.get('strategy', ''),
        )
        # 压缩后 history 投影变了, 旧 _ov_id 可能不在新 history 里 → reset OV cursor.
        if self._ov is not None and self._ov.enabled:
            self._ov.reset_cursor()
        return {'compacted': True}
