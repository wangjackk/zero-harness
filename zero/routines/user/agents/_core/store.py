"""Store -- sqlite backend for agent history + agent records.

Centralized db: ~/.zero/sessions.db. All agents/sessions/messages across
all projects live in one db, so an agent's identity and history stay
continuous as it moves between projects. One background writer thread +
queue (appends never block the async loop), flush() barrier before reads,
RLock + _tx() for serialized writes.

agent = session 模型:
  - 每个 session 就是一个 agent (agent_id == session_id)
  - agents 表只存历史元数据 (agent_id, model, title, created_at, updated_at)
  - 无 sessions 表 (无线性链)
  - messages 表按 (agent_id, session_id) 索引, agent=session 后两者相等

Two tables:
  messages  - chronological event log (id ASC = write order). Each row's `data`
              is the full entry JSON (the event-shape consumed by replay).
  agents    - 历史元数据 (agent_id, model, title, created_at, updated_at).
              持久化以让 list_agents 跨重启存活; 运行时状态 (handle/live 等)
              由 manager 内存维护, 不入表.
"""
from __future__ import annotations

import json
import os
import queue
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

# Centralized db location: ~/.zero/sessions.db.
# Override via ZERO_SESSION_DB env var (for tests / custom installations).
_CENTRAL_DB_DIR = Path(os.environ.get(
    'ZERO_SESSION_DIR',
    str(Path.home() / '.zero'),
))
_CENTRAL_DB_PATH = _CENTRAL_DB_DIR / 'sessions.db'


class Store:
    """sqlite store for agent history + agent records.

    Writes are non-blocking: insert_message/_raw_insert push a write closure onto
    a background thread's queue and return immediately. Reads call flush() first
    (a barrier that blocks until all queued writes are applied), so they observe
    every prior write. Thread safety via a single RLock serializing _tx() blocks.
    """

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
    ) -> None:
        """init store.

        db 路径解析 (集中存储):
          1. db_path 显式指定 (测试用)
          2. ZERO_SESSION_DB 环境变量
          3. ~/.zero/sessions.db (默认)
        """
        if db_path is not None:
            self._path = Path(db_path)
        elif os.environ.get('ZERO_SESSION_DB'):
            self._path = Path(os.environ['ZERO_SESSION_DB'])
        else:
            self._path = _CENTRAL_DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()
        # background writer: serially applies queued write closures (FIFO ->
        # preserves write order; reuses _tx() lock semantics).
        self._write_q: "queue.Queue[Optional[tuple[Callable[[], None], Optional[threading.Event]]]]" = queue.Queue()
        self._writer_stop = threading.Event()
        self._writer = threading.Thread(
            target=self._write_loop, name='agent-store-writer', daemon=True,
        )
        self._writer.start()

    # ------------------------------------------------------------------
    # internal: connection / transaction / writer thread
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _tx(self):
        """open conn -> yield -> commit/close. rollback on exception."""
        with self._lock:
            conn = self._conn()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _write_loop(self) -> None:
        while True:
            item = self._write_q.get()
            if item is None:
                return
            fn, after = item
            try:
                fn()
            except Exception:
                # sqlite write failures are rare; swallow to avoid killing the
                # writer thread (which would silently drop all later writes).
                pass
            if after is not None:
                after.set()

    def _submit_write(self, fn: Callable[[], None]) -> None:
        """queue a write closure on the background writer; return immediately."""
        self._write_q.put((fn, None))

    def flush(self) -> None:
        """block until all queued writes are applied. call before any read."""
        barrier = threading.Event()
        self._write_q.put((lambda: None, barrier))
        barrier.wait()

    def close(self) -> None:
        """stop the background writer. mainly for tests."""
        self._writer_stop.set()
        self._write_q.put(None)
        self._writer.join(timeout=2.0)

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._tx() as c:
            # --- messages: 时间序事件日志 ---
            # agent_id 是 agent 身份 (prime_12 等), session_id 是会话身份 (UUID).
            # 两者解耦: 一个 agent 跨 resume 复用同一 session_id, 压缩不改 session_id.
            c.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id   TEXT    NOT NULL,
                    session_id TEXT    NOT NULL,
                    type       TEXT    NOT NULL,
                    ts         TEXT    NOT NULL,
                    data       TEXT    NOT NULL DEFAULT '{}'
                )
            ''')
            c.execute(
                'CREATE INDEX IF NOT EXISTS idx_msg_agent_session '
                'ON messages(agent_id, session_id, id)'
            )

            # --- agents: 历史元数据 (无运行时状态列) ---
            # status / handle_id / peer_seq 等运行时字段不入表,
            # 由 manager 内存维护; 跨重启只剩元数据 + session_id.
            # session_id 列: UUID, 创建时生成, resume 时复用 (保证消息流连续).
            c.execute('''
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id    TEXT PRIMARY KEY,
                    session_id  TEXT,
                    model       TEXT,
                    title       TEXT,
                    created_at  TEXT    NOT NULL,
                    updated_at  TEXT    NOT NULL
                )
            ''')
            # migration: 已有 DB 的 agents 表补 session_id 列
            try:
                c.execute('ALTER TABLE agents ADD COLUMN session_id TEXT')
            except Exception:
                pass  # 列已存在

    # ------------------------------------------------------------------
    # messages
    # ------------------------------------------------------------------

    def insert_message(
        self,
        agent_id: str,
        session_id: str,
        entry: dict[str, Any],
    ) -> None:
        """append one event row. stamps ts (utc now) + uuid if missing.

        also bumps agents.updated_at and seeds agents.title from the first real
        user message (content[:60]) -- both no-ops if the agents row does not
        exist yet (it is created later by register_agent).
        """
        entry = dict(entry)
        entry.setdefault('uuid', _entry_uuid(entry))
        entry.setdefault('ts', _now_iso())
        typ = str(entry.get('type') or '')
        data = json.dumps(entry, ensure_ascii=False, default=str)
        ts = str(entry.get('ts') or _now_iso())

        content = str(entry.get('content') or '') if typ == 'user' else ''
        agent_id_, session_id_, typ_, data_, ts_ = agent_id, session_id, typ, data, ts

        def _write() -> None:
            with self._tx() as c:
                c.execute(
                    'INSERT INTO messages(agent_id, session_id, type, ts, data) '
                    'VALUES(?, ?, ?, ?, ?)',
                    (agent_id_, session_id_, typ_, ts_, data_),
                )
                c.execute(
                    'UPDATE agents SET updated_at = ? WHERE agent_id = ?',
                    (ts_, agent_id_),
                )
                if content:
                    c.execute(
                        'UPDATE agents SET title = ? '
                        'WHERE agent_id = ? AND (title IS NULL OR title = ?)',
                        (content[:60], agent_id_, ''),
                    )

        self._submit_write(_write)

    def _raw_insert(
        self,
        agent_id: str,
        session_id: str,
        entry_type: str,
        ts: str,
        data_str: str,
    ) -> None:
        """insert a pre-serialized row (migration path preserving original ts/uuid)."""
        agent_id_, session_id_, typ_, ts_, data_ = agent_id, session_id, entry_type, ts, data_str

        def _write() -> None:
            with self._tx() as c:
                c.execute(
                    'INSERT INTO messages(agent_id, session_id, type, ts, data) '
                    'VALUES(?, ?, ?, ?, ?)',
                    (agent_id_, session_id_, typ_, ts_, data_),
                )

        self._submit_write(_write)

    def replay(
        self,
        agent_id: str,
        session_id: str,
        *,
        cwd: str,
        project_root: str | None,
        model: str | None,
        reasoning_effort: str | None = None,
        plan_mode: bool = False,
    ) -> tuple[Any, list[dict[str, Any]], dict[str, Any] | None]:
        """replay a session's events into (SessionState, items, response_state).

        flush() first so all queued writes are visible, then walk messages in
        chronological order rebuilding the same structures the old JSONL reader
        produced. helpers (_apply_state_snapshot/_parse_todos/SessionState) are
        imported lazily from .session to avoid a circular import at module load.

        compaction 投影: 找最后一个 type='compaction' 的 entry, 按 preserve_from_id
        投影 -- 该 id 之前的消息被 summary 替代(不发给 LLM), 之后的原样保留.
        原始消息不从 DB 删除, 可回溯/可重新压缩.
        """
        self.flush()
        with self._tx() as c:
            rows = c.execute(
                'SELECT id, type, data FROM messages '
                'WHERE agent_id = ? AND session_id = ? ORDER BY id ASC',
                (agent_id, session_id),
            ).fetchall()

        # lazy import: .session imports .session_writer which imports this module
        # at top level; importing here (call time) breaks the load-time cycle.
        from .session import SessionState, _apply_state_snapshot, _parse_todos

        state = SessionState(
            session_id=session_id,
            cwd=cwd,
            project_root=project_root,
            model=model,
            reasoning_effort=reasoning_effort,
            plan_mode=plan_mode,
        )
        items: list[dict[str, Any]] = []
        response_state: dict[str, Any] | None = None

        # 找最后一个 compaction entry (如果有)
        last_compaction: dict[str, Any] | None = None
        last_compaction_row_id: int = 0
        for row in rows:
            try:
                entry = json.loads(row['data'])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(entry, dict) and entry.get('type') == 'compaction':
                last_compaction = entry
                last_compaction_row_id = int(row['id'])

        if last_compaction is not None:
            preserve_from_id = int(last_compaction.get('preserve_from_id') or 0)
            summary = str(last_compaction.get('summary') or '')
            # 投影: summary_msg + preserve_from_id 之后的原始消息
            if summary:
                items.append({
                    'role': 'user',
                    'content': f'Context summary:\n\n{summary}',
                })
            for row in rows:
                row_id = int(row['id'])
                # 跳过 compaction entry 本身
                if row_id == last_compaction_row_id:
                    continue
                # 跳过被压缩的消息 (preserve_from_id 之前的, 以及更早的 compaction)
                if row_id < preserve_from_id:
                    continue
                try:
                    entry = json.loads(row['data'])
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(entry, dict):
                    continue
                typ = entry.get('type')
                # 跳过更早的 compaction entry (保留区间内不该出现, 防御性)
                if typ == 'compaction':
                    continue
                if typ == 'session_start':
                    state.cwd = str(entry.get('cwd') or state.cwd)
                    state.model = entry.get('model') or state.model
                    state.plan_mode = bool(entry.get('plan_mode', state.plan_mode))
                elif typ == 'session_state':
                    _apply_state_snapshot(state, entry.get('state'))
                elif typ == 'todo_update':
                    state.todos = _parse_todos(entry.get('new_todos') or entry.get('todos') or [])
                else:
                    _append_item(items, entry)
                if typ == 'response_checkpoint':
                    response_id = str(entry.get('response_id') or '')
                    if response_id:
                        response_state = {
                            'response_id': response_id,
                            'text': str(entry.get('text') or ''),
                            'usage': entry.get('usage') if isinstance(entry.get('usage'), dict) else None,
                            'cursor': len(items),
                        }
            return state, items, response_state

        # 无 compaction, 全量 replay
        for row in rows:
            try:
                entry = json.loads(row['data'])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(entry, dict):
                continue
            typ = entry.get('type')
            if typ == 'session_start':
                state.cwd = str(entry.get('cwd') or state.cwd)
                state.model = entry.get('model') or state.model
                state.plan_mode = bool(entry.get('plan_mode', state.plan_mode))
            elif typ == 'session_state':
                _apply_state_snapshot(state, entry.get('state'))
            elif typ == 'todo_update':
                state.todos = _parse_todos(entry.get('new_todos') or entry.get('todos') or [])
            else:
                _append_item(items, entry)
            if typ == 'response_checkpoint':
                response_id = str(entry.get('response_id') or '')
                if response_id:
                    response_state = {
                        'response_id': response_id,
                        'text': str(entry.get('text') or ''),
                        'usage': entry.get('usage') if isinstance(entry.get('usage'), dict) else None,
                        'cursor': len(items),
                    }

        return state, items, response_state

    # ------------------------------------------------------------------
    # compaction
    # ------------------------------------------------------------------

    def get_last_message_id(self, agent_id: str, session_id: str) -> int:
        """返回指定 session 最后一条消息的 DB row id (用于 compaction 的 preserve_from_id).

        flush() 后读, 保证已入队的写都可见. 无消息时返回 0.
        """
        self.flush()
        with self._tx() as c:
            row = c.execute(
                'SELECT id FROM messages '
                'WHERE agent_id = ? AND session_id = ? '
                'ORDER BY id DESC LIMIT 1',
                (agent_id, session_id),
            ).fetchone()
        return int(row['id']) if row else 0

    def write_compaction(
        self,
        agent_id: str,
        session_id: str,
        *,
        summary: str,
        preserve_from_id: int,
        strategy: str,
        tokens_before: int = 0,
        tokens_after: int = 0,
    ) -> None:
        """追加一个 compaction entry 到 messages 表.

        不删除/不修改任何已有消息. replay() 会找最后一个 compaction entry 做投影:
        preserve_from_id 之前的消息被 summary 替代(不发给 LLM), 之后的原样保留.

        调用前必须确保 agent 已把内存态全部 flush 到 DB (调 writer.flush / store.flush),
        否则 preserve_from_id 会漏掉未写入的最新消息.
        """
        entry: dict[str, Any] = {
            'type': 'compaction',
            'strategy': strategy,
            'summary': summary,
            'preserve_from_id': preserve_from_id,
            'tokens_before': tokens_before,
            'tokens_after': tokens_after,
        }
        # insert_message 会补 uuid + ts, 并 bump agents.updated_at
        self.insert_message(agent_id, session_id, entry)

    # ------------------------------------------------------------------
    # agents
    # ------------------------------------------------------------------

    def register_agent(
        self, agent_id: str, *, session_id: str | None = None, model: str | None = None,
    ) -> None:
        """upsert 一个 agents 行 (存元数据 + session_id).

        session_id: 会话身份 (UUID). create 时传入新 UUID; resume 时传入从
        get_agent 读出的旧 UUID (保证消息流连续). 仅在 INSERT 时写入,
        UPDATE 不覆盖已有 session_id (防止 resume 误传空值清掉).
        INSERT OR IGNORE 保留 created_at (resume 场景), 再 UPDATE model + updated_at.
        """
        now = _now_iso()
        agent_id_, session_id_, model_, now_ = agent_id, session_id, model, now

        def _write() -> None:
            with self._tx() as c:
                c.execute(
                    'INSERT OR IGNORE INTO agents'
                    '(agent_id, session_id, model, created_at, updated_at) '
                    'VALUES(?, ?, ?, ?, ?)',
                    (agent_id_, session_id_, model_, now_, now_),
                )
                c.execute(
                    'UPDATE agents SET model = ?, updated_at = ? '
                    'WHERE agent_id = ?',
                    (model_, now_, agent_id_),
                )

        self._submit_write(_write)

    def list_agents(self) -> list[dict[str, Any]]:
        """all agent rows, newest-updated first (返回元数据 + session_id)."""
        self.flush()
        with self._tx() as c:
            rows = c.execute(
                'SELECT agent_id, session_id, model, title, created_at, updated_at '
                'FROM agents ORDER BY updated_at DESC'
            ).fetchall()
        return [dict(r) for r in rows]

    def get_last_reasoning_effort(self, agent_id: str) -> str | None:
        """读最后一条 session_state snapshot 的 reasoning_effort.

        set_effort 时同步写 snapshot, 故此值即当前 effort, 跨 live/stopped 都可用.
        无 session_state entry (新 agent 未交互过) 返回 None.
        """
        self.flush()
        with self._tx() as c:
            row = c.execute(
                "SELECT data FROM messages "
                "WHERE agent_id = ? AND type = 'session_state' "
                "ORDER BY id DESC LIMIT 1",
                (agent_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row['data'])
        except (json.JSONDecodeError, TypeError):
            return None
        state = data.get('state') if isinstance(data, dict) else None
        if not isinstance(state, dict):
            return None
        effort = state.get('reasoning_effort')
        return str(effort) if effort else None

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        self.flush()
        with self._tx() as c:
            row = c.execute(
                'SELECT agent_id, session_id, model, title, created_at, updated_at '
                'FROM agents WHERE agent_id = ?',
                (agent_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def delete_agent(self, agent_id: str) -> None:
        """删除 agent: agents 行 + 该 agent 的所有 messages.

        messages 按 agent_id 匹配删除 (agent_id 跨 session 唯一).
        不可恢复, 调用前需确保 agent 已 stop.
        """
        agent_id_ = agent_id

        def _write() -> None:
            with self._tx() as c:
                c.execute(
                    'DELETE FROM messages WHERE agent_id = ?', (agent_id_,)
                )
                c.execute(
                    'DELETE FROM agents WHERE agent_id = ?', (agent_id_,)
                )

        self._submit_write(_write)

    # ------------------------------------------------------------------
    # session 消息读取 (agent_id + session_id 联合查询)
    # ------------------------------------------------------------------

    def iter_session_messages(self, agent_id: str, session_id: str) -> list[dict[str, Any]]:
        """return all stored entries for a session in chronological order.

        used by convert_session_to_mempalace (derived-format exporter).
        """
        self.flush()
        with self._tx() as c:
            rows = c.execute(
                'SELECT data FROM messages WHERE agent_id = ? AND session_id = ? '
                'ORDER BY id ASC',
                (agent_id, session_id),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                entry = json.loads(r['data'])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(entry, dict):
                out.append(entry)
        return out

    def iter_session_rows(
        self, agent_id: str, session_id: str,
    ) -> list[tuple[int, dict[str, Any]]]:
        """返回 [(row_id, entry_dict), ...] 按时间顺序.

        供 condenser agent 使用: 需要知道每条消息的 DB row id,
        以便压缩时把 cut_index 映射到 preserve_from_id.
        """
        self.flush()
        with self._tx() as c:
            rows = c.execute(
                'SELECT id, data FROM messages '
                'WHERE agent_id = ? AND session_id = ? ORDER BY id ASC',
                (agent_id, session_id),
            ).fetchall()
        out: list[tuple[int, dict[str, Any]]] = []
        for r in rows:
            try:
                entry = json.loads(r['data'])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(entry, dict):
                out.append((int(r['id']), entry))
        return out


# ----------------------------------------------------------------------
# module-level single store instance (centralized db).
# ----------------------------------------------------------------------
# 集中存储: 一个进程只有一个 Store (对应 ~/.zero/sessions.db).
# tests use set_test_store() to inject a temp store bypassing the singleton.
_store: Optional[Store] = None
# test override: when set, get_store() returns this regardless.
_test_store: Optional[Store] = None


def get_store() -> Store:
    """return the singleton Store (centralized db at ~/.zero/sessions.db)."""
    global _store
    if _test_store is not None:
        return _test_store
    if _store is None:
        _store = Store()
    return _store


def set_test_store(store: Optional[Store]) -> None:
    """inject a temp store for tests (bypasses the singleton). closes the
    previous test store when replaced/cleared."""
    global _test_store
    if _test_store is not None:
        try:
            _test_store.close()
        except Exception:
            pass
    _test_store = store


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entry_uuid(entry: dict[str, Any]) -> str:
    """stable uuid per entry: call_id-derived for FC/FCO, else random."""
    typ = entry.get('type')
    call_id = str(entry.get('call_id') or '')
    if typ == 'function_call' and call_id:
        return call_id
    if typ == 'function_call_output' and call_id:
        return f'{call_id}_output'
    return uuid4().hex


def _append_item(items: list[dict[str, Any]], entry: dict[str, Any]) -> None:
    """把 entry 转成 LLM input item.

    只处理 user/assistant/function_call/function_call_output 四种.
    其他 (response_checkpoint/session_*/compaction) 跳过.
    """
    typ = entry.get('type')
    if typ == 'user':
        items.append({
            'role': 'user',
            'content': str(entry.get('content') or ''),
        })
    elif typ == 'assistant':
        content = str(entry.get('content') or '')
        if content:
            items.append({
                'role': 'assistant',
                'content': content,
            })
    elif typ == 'function_call':
        items.append({
            'type': 'function_call',
            'name': str(entry.get('name') or ''),
            'arguments': str(entry.get('arguments') or ''),
            'call_id': str(entry.get('call_id') or ''),
        })
    elif typ == 'function_call_output':
        items.append({
            'type': 'function_call_output',
            'call_id': str(entry.get('call_id') or ''),
            'output': str(entry.get('output') or ''),
        })
