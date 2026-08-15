"""XmlAgent 记忆 ---- 极简 sqlite.

对比老 ``AgentMemory``(树 + yaml 双写双读 + 流式缓冲 + 单例 + save/restore),
这里只有一张表,一把锁,四个方法.无树,无 yaml,无单例,无持久化镜像分层.

DB 文件 ``runtime/xml_agent/memory.db``,**重启保留**(老 agent 启动清空,这是升级).
"""
from __future__ import annotations

import json
import queue
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    """ISO-8601 UTC 时间戳 (前端按字符串降序排序)."""
    return datetime.now(timezone.utc).isoformat()
from typing import Callable, Optional
from uuid import uuid4

_DB_PATH = Path('runtime/xml_agent/memory.db')


# 模块级单例: Memory() 构造时启动后台写线程, 多次 new 会泄漏线程.
# agent / manager / condenser 共用同一个实例 (读写同一 sqlite 文件, RLock 串行化).
_INSTANCE: 'Memory | None' = None


def get_memory() -> 'Memory':
    """返回模块级 Memory 单例 (首次调用惰性构造)."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = Memory()
    return _INSTANCE


class Memory:
    """对话历史的 sqlite 后端.

    线程安全靠一把 ``RLock`` 串行化所有写 + 读.v1 单 react task 串行调用足够;
    若后续接并发读取(如 MemoryControl 类 routine),已有锁兜底.

    写不阻塞主循环:``add_message`` 把写操作投到后台写线程的队列即返回,
    事件循环不再为 sqlite IO 等待(典型 8ms → ~0ms).读方法(``load_history``
    等)在返回前自动 ``flush()``,保证读到所有已投递的写.
    """

    def __init__(self, db_path: str | Path = _DB_PATH) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()
        # 后台写线程:串行执行投递过来的写闭包,串行保证写顺序 + 复用 _tx 的锁语义.
        self._write_q: "queue.Queue[Optional[Callable[[], None]]]" = queue.Queue()
        self._writer_stop = threading.Event()
        self._writer = threading.Thread(
            target=self._write_loop, name='xml-memory-writer', daemon=True,
        )
        self._writer.start()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _tx(self):
        """开连接 → yield → commit/close.异常时 rollback(sqlite3 连接 __exit__ 行为)."""
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

    # ------------------------------------------------------------------
    # 后台写线程
    # ------------------------------------------------------------------

    def _write_loop(self) -> None:
        """后台写线程:从队列取 (写闭包, 完成回调) 串行执行."""
        while True:
            fn, after = self._write_q.get()
            try:
                fn()
            except Exception:
                # sqlite 写失败极少见;后台吞掉避免拖垮整个进程.
                pass
            if after is not None:
                after()

    def _submit_write(self, fn: Callable[[], None]) -> None:
        """投一个写到后台线程,立即返回(不阻塞事件循环)."""
        self._write_q.put((fn, None))

    def flush(self) -> None:
        """阻塞等到所有已投递的写完成.读方法调用前用它保证读到最新.

        投一个 barrier 闭包到队尾并等它被执行----它被处理时,之前所有写
        必已串行执行完(队列 FIFO).
        """
        barrier = threading.Event()
        self._write_q.put((lambda: None, barrier.set))
        barrier.wait()

    def _init_schema(self) -> None:
        with self._tx() as c:
            c.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id  TEXT    NOT NULL,
                    session_id  TEXT    NOT NULL,
                    role        TEXT    NOT NULL,
                    content     TEXT    NOT NULL DEFAULT '',
                    interrupted INTEGER NOT NULL DEFAULT 0,
                    ts          TEXT    NOT NULL,
                    feedback    TEXT,
                    results_raw TEXT,
                    extend_data TEXT,
                    response_id TEXT,
                    model       TEXT
                )
            ''')
            c.execute(
                'CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)'
            )
            # agents 表: 每个独立, 无线性链.
            # status/handle_id 是运行时态 (manager 内存管), 不持久化.
            # session_id 列: UUID, 创建时生成, resume 时复用 (保证消息流连续).
            c.execute('''
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id   TEXT PRIMARY KEY,
                    session_id TEXT,
                    model      TEXT,
                    title      TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            ''')
            # migration: 已有 DB 的 agents 表补 session_id 列
            try:
                c.execute('ALTER TABLE agents ADD COLUMN session_id TEXT')
            except sqlite3.OperationalError:
                pass  # 列已存在
            # kind 列: NULL = 普通消息, 'summary' = 上下文压缩摘要.
            # summary 消息跟普通消息同表, role='user' (LLM 输入格式), content=摘要文本,
            # extend_data 存覆盖范围 {covered_from, covered_to}.
            # OV 上传跳过 kind='summary'; 前端展示跳过; LLM 投影从最后一条 summary 开始取.
            try:
                c.execute('ALTER TABLE messages ADD COLUMN kind TEXT')
            except sqlite3.OperationalError:
                pass  # 列已存在 (老 DB migrate)

    # legacy single-instance session API removed (multi-instance: one agent = one session; sessions table kept for compat reads)

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
        extend_data: Optional[list] = None,
        response_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> int:
        """追加一条消息,返回自增 id.三层数据分离:

        - ``feedback``:**给 LLM** 的 ---- 经 ``for_llm`` 过滤后的子 routine 结果
          (说话类被 ``for_llm:null`` 抑制).load_history 时取这个喂 LLM.
        - ``results_raw``:**原始** ---- 所有子 routine 的 ``{name, result, error}``,
          不过滤(含 output 说话类的 frames/elapsed_ms 等).诊断/审计用,不喂 LLM.
        - ``extend_data``:**链路记账** ---- 各子 routine 的 ``{name, extend_data}``
          (token usage / span 时间等元数据,见 RoutineHandle.extend_data).不喂 LLM.
        - ``response_id``:该 assistant 消息对应 LLM response 的 id(Responses API).
          用于 ``previous_response_id`` 做 prompt caching ---- 下次只发该 response 之后的
          增量 + 传 previous_response_id 复用服务端缓存的 prefix.打断的轮次不存
          response_id(没拿到 Completed),cursor 自动回退到上一条有 id 的.

        四者都按需存入各自列;user 消息通常全空.
        """
        ts = _now_iso()
        fb = json.dumps(feedback, ensure_ascii=False) if feedback else None
        rr = json.dumps(results_raw, ensure_ascii=False) if results_raw else None
        ed = json.dumps(extend_data, ensure_ascii=False) if extend_data else None
        role_, content_ = role, content
        sid, mid, aid = session_id, message_id, agent_id

        def _write() -> None:
            with self._tx() as c:
                cur = c.execute(
                    'INSERT INTO messages(message_id, session_id, role, content, '
                    'interrupted, ts, feedback, results_raw, extend_data, response_id, model) '
                    'VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (mid, sid, role_, content_ or '',
                     1 if interrupted else 0, ts, fb, rr, ed, response_id, model),
                )
                c.execute(
                    'UPDATE agents SET updated_at = ? WHERE agent_id = ?',
                    (ts, aid),
                )
                if role_ == 'user' and not content_.startswith('[feedback]'):
                    arow = c.execute(
                        'SELECT title FROM agents WHERE agent_id = ?', (aid,)
                    ).fetchone()
                    if arow is not None and not arow['title']:
                        title = (content_ or '').strip()[:60]
                        if title:
                            c.execute(
                                'UPDATE agents SET title = ? WHERE agent_id = ?',
                                (title, aid),
                            )

        self._submit_write(_write)

    def load_history(
        self,
        session_id: str,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """按 id 升序返回指定 session 的 history(给 LLM 拼 messages 用).

        limit 取最近 N 条(仍保持时序). 含 kind='summary' 消息 (agent 侧投影切).
        summary 消息的 extend_data 解析后以 dict 返回 (含 covered_from/to).
        """
        self.flush()
        with self._tx() as c:
            if limit is not None:
                rows = c.execute(
                    'SELECT role, content, interrupted, feedback, message_id, '
                    'response_id, kind, extend_data '
                    'FROM messages WHERE session_id = ? '
                    'ORDER BY id DESC LIMIT ?',
                    (session_id, int(limit)),
                ).fetchall()
                rows = list(reversed(rows))
            else:
                rows = c.execute(
                    'SELECT role, content, interrupted, feedback, message_id, '
                    'response_id, kind, extend_data '
                    'FROM messages WHERE session_id = ? '
                    'ORDER BY id ASC',
                    (session_id,),
                ).fetchall()
        out: list[dict] = []
        for r in rows:
            fb = None
            if r['feedback']:
                try:
                    fb = json.loads(r['feedback'])
                except Exception:
                    fb = None
            extend = None
            if r['extend_data']:
                try:
                    extend = json.loads(r['extend_data'])
                except Exception:
                    extend = None
            out.append({
                'message_id': r['message_id'],
                'role': r['role'],
                'content': r['content'],
                'interrupted': bool(r['interrupted']),
                'feedback': fb,
                'response_id': r['response_id'],
                'kind': r['kind'],
                'extend_data': extend,
                # OV 推送对齐用 (跟 prime 的 _ov_id 统一).
                # _ 前缀 = 内部字段, _build_messages 只取 role/content 不会泄露给 LLM.
                '_ov_id': r['message_id'],
            })
        return out


    def load_messages(self, session_id: str) -> list[dict]:
        """Return session messages in the general chat format (role/text).

        Converts the LLM-oriented storage format into general messages:
        - real user message -> {role:'user', text}
        - ``[feedback]`` pseudo user message -> skipped (already shown as a
          tool block attached to the preceding assistant message)
        - ``kind='summary'`` message -> skipped (internal context compression,
          not for user display)
        - assistant message -> {role:'assistant', text} followed by
          {role:'tool', results} when it has feedback
        """
        self.flush()
        with self._tx() as c:
            rows = c.execute(
                'SELECT message_id, role, content, feedback, kind '
                'FROM messages WHERE session_id = ? ORDER BY id ASC',
                (session_id,),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            role = r['role']
            content = r['content'] or ''
            mid = r['message_id']
            if r['kind'] == 'summary':
                continue
            if role == 'user':
                if content.startswith('[feedback]'):
                    continue
                out.append({
                    'id': f'hist-user-{mid}',
                    'role': 'user',
                    'text': content,
                    'final': True,
                })
            elif role == 'assistant':
                out.append({
                    'id': f'hist-asst-{mid}',
                    'role': 'assistant',
                    'text': content,
                    'final': True,
                })
                fb = None
                if r['feedback']:
                    try:
                        fb = json.loads(r['feedback'])
                    except Exception:
                        fb = None
                if fb:
                    results: list[dict] = []
                    for item in fb:
                        name = str(item.get('name') or '').strip()
                        value = str(item.get('result') or '').strip()
                        if not name or not value:
                            continue
                        entry: dict = {'name': name, 'status': 'done'}
                        if item.get('is_error'):
                            entry['error'] = {'msg': value}
                        else:
                            entry['result'] = value
                        results.append(entry)
                    if results:
                        out.append({
                            'id': f'hist-tool-{mid}',
                            'role': 'tool',
                            'text': '',
                            'results': results,
                            'final': True,
                        })
        return out

    def get_last_response_id(self, session_id: str, *, model: str | None = None) -> str | None:
        """返回该 session 最后一条带 response_id 的 assistant 消息的 response_id.

        用于 ``previous_response_id``(prompt caching)---- 下次调 LLM 只发该 response
        之后的增量 + 传它复用服务端缓存.无(首启 / 全被打断)返回 None → 全量发.

        ``model`` 非空时,只在最后一条 response_id 的 model 匹配时才返回----
        response_id 是 model 绑定的,跨 model 复用会导致 API 报错 / 缓存失效.
        老消息无 model 列(NULL)→ 视为不匹配,安全回退到全量发.
        """
        self.flush()
        with self._tx() as c:
            row = c.execute(
                "SELECT response_id, model FROM messages "
                "WHERE session_id = ? AND response_id IS NOT NULL AND response_id != '' "
                "ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if row is None or not row['response_id']:
            return None
        if model is not None and row['model'] != model:
            return None
        return row['response_id']

    def clear_last_response_id(self, session_id: str, response_id: str) -> None:
        """清掉某条 assistant 消息的 response_id(置 NULL).

        服务端 Responses API 缓存有 TTL(doubao ~30min),过期后用该 response_id 做
        ``previous_response_id`` 会报 ``InvalidParameter.PreviousResponseNotFound``.
        agent 捕获该错后调本方法清掉过期 id -> 下次 ``get_last_response_id`` 回退到
        更早的(或 None 全量发),自动恢复,无需重启 / 改 session.

        按 response_id 精确定位(不误清别的轮次),同步写(走 _tx 不投后台--agent
        重试前必须清干净,否则下一轮还会撞同一个过期 id).
        """
        with self._tx() as c:
            c.execute(
                "UPDATE messages SET response_id = NULL "
                "WHERE session_id = ? AND response_id = ?",
                (session_id, response_id),
            )

    def clear(self, session_id: str) -> None:
        """clear all messages of one session.

        session_id is required -- no "clear all" convenience entry, to avoid
        accidental mass deletion.
        """
        with self._tx() as c:
            c.execute('DELETE FROM messages WHERE session_id = ?', (session_id,))

    def delete(self, ids: list[int]) -> int:
        """按自增 id 批量删除,返回删除条数."""
        if not ids:
            return 0
        placeholders = ','.join('?' * len(ids))
        with self._tx() as c:
            cur = c.execute(
                f'DELETE FROM messages WHERE id IN ({placeholders})',
                [int(i) for i in ids],
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # summary 消息 (kind='summary', 跟普通消息同表)
    # ------------------------------------------------------------------

    def add_summary_message(
        self,
        session_id: str,
        *,
        message_id: str,
        summary: str,
        covered_from: str,
        covered_to: str,
        strategy: str = '',
        tokens_before: int = 0,
        tokens_after: int = 0,
    ) -> None:
        """写一条上下文压缩摘要消息到 messages 表.

        - ``kind='summary'`` 标识 (OV 上传跳过, 前端展示跳过, LLM 投影从此切).
        - ``role='user'`` 保持 LLM 输入二元格式, content='Context summary:\\n\\n...'.
        - ``extend_data`` 存覆盖范围 + 元数据:
          ``{covered_from, covered_to, strategy, tokens_before, tokens_after}``.

        summary 插在 head 末尾 (covered_to) 之后, tail 第一条之前.
        多次压缩追加多行 summary; LLM 投影用最后一条 (load_history 时序最后).
        """
        ts = _now_iso()
        content = f'Context summary:\n\n{summary}'
        extend = json.dumps({
            'covered_from': covered_from,
            'covered_to': covered_to,
            'strategy': strategy,
            'tokens_before': tokens_before,
            'tokens_after': tokens_after,
        }, ensure_ascii=False)

        def _write() -> None:
            with self._tx() as c:
                c.execute(
                    'INSERT INTO messages(message_id, session_id, role, '
                    'content, interrupted, ts, extend_data, kind) '
                    'VALUES(?, ?, ?, ?, ?, ?, ?, ?)',
                    (message_id, session_id, 'user', content, 0, ts, extend, 'summary'),
                )

        self._submit_write(_write)

    # ------------------------------------------------------------------
    # agents 表管理 (agent = session, 每个独立)
    # ------------------------------------------------------------------

    def next_agent_id(self, prefix: str = 'xml') -> str:
        """生成下一个自增 agent_id: ``<prefix>_<N>``.

        N = 当前 DB 中以 ``<prefix>_`` 开头且后缀为纯数字的 agent_id 的最大数字 + 1.
        用 prefix 区分 kind (xml_1 / xml_2 ...), 便于 agent 间互相称呼.
        """
        self.flush()
        pfx = f'{prefix}_'
        with self._tx() as c:
            rows = c.execute(
                'SELECT agent_id FROM agents WHERE agent_id LIKE ?',
                (f'{pfx}%',),
            ).fetchall()
        max_n = 0
        for r in rows:
            suffix = str(r['agent_id'])[len(pfx):]
            if suffix.isdigit():
                n = int(suffix)
                if n > max_n:
                    max_n = n
        return f'{pfx}{max_n + 1}'

    def register_agent(
        self, agent_id: str, *, session_id: str | None = None, model: str | None = None,
    ) -> None:
        """upsert 一行 agent. resume 时保留 created_at.

        session_id: 会话身份 (UUID). create 时传入新 UUID; resume 时传入从
        get_agent 读出的旧 UUID. 仅在 INSERT 时写入, UPDATE 不覆盖.
        """
        ts = _now_iso()
        with self._tx() as c:
            c.execute(
                'INSERT INTO agents(agent_id, session_id, model, '
                'created_at, updated_at) VALUES(?, ?, ?, ?, ?) '
                'ON CONFLICT(agent_id) DO UPDATE SET '
                'model=excluded.model, updated_at=excluded.updated_at',
                (agent_id, session_id, model, ts, ts),
            )

    def get_agent(self, agent_id: str) -> dict | None:
        self.flush()
        with self._tx() as c:
            row = c.execute(
                'SELECT agent_id, session_id, model, title, '
                'created_at, updated_at FROM agents WHERE agent_id = ?',
                (agent_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def delete_agent(self, agent_id: str) -> None:
        """删除 agent: agents 行 + 该 session 的所有 messages.

        messages 按 session_id 匹配删除 (session_id 跨 agent 唯一, 含 summary 消息).
        不可恢复, 调用前需确保 agent 已 stop. 需先 get_agent 拿 session_id.
        """
        agent_id_ = agent_id
        self.flush()
        with self._tx() as c:
            row = c.execute(
                'SELECT session_id FROM agents WHERE agent_id = ?', (agent_id_,)
            ).fetchone()
        session_id_ = row['session_id'] if row is not None else None

        def _write() -> None:
            with self._tx() as c:
                if session_id_ is not None:
                    c.execute(
                        'DELETE FROM messages WHERE session_id = ?', (session_id_,)
                    )
                c.execute(
                    'DELETE FROM agents WHERE agent_id = ?', (agent_id_,)
                )

        self._submit_write(_write)

    def list_agents(self) -> list[dict]:
        self.flush()
        with self._tx() as c:
            rows = c.execute(
                'SELECT agent_id, session_id, model, title, '
                'created_at, updated_at FROM agents ORDER BY updated_at DESC'
            ).fetchall()
        return [dict(r) for r in rows]

    def set_agent_title(self, agent_id: str, title: str) -> None:
        """更新 agent 标题 (通常从首条 user message 提取)."""
        ts = _now_iso()
        with self._tx() as c:
            c.execute(
                'UPDATE agents SET title = ?, updated_at = ? WHERE agent_id = ?',
                (title, ts, agent_id),
            )
