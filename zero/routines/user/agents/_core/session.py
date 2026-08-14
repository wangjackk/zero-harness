"""Session state and replay support for the prime agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .response_tracker import ResponseTracker, TurnMeta
from .session_writer import SessionWriter

SESSION_ID_KEY = 'session_id'

TodoStatus = Literal['pending', 'in_progress', 'completed']


@dataclass
class TodoItem:
    content: str
    status: TodoStatus
    active_form: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'TodoItem':
        status = str(data.get('status') or 'pending')
        if status not in ('pending', 'in_progress', 'completed'):
            status = 'pending'
        return cls(
            content=str(data.get('content') or ''),
            status=status,  # type: ignore[arg-type]
            active_form=(
                str(data.get('active_form') or data.get('activeForm'))
                if data.get('active_form') or data.get('activeForm')
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            'content': self.content,
            'status': self.status,
        }
        if self.active_form:
            result['active_form'] = self.active_form
        return result


@dataclass
class SessionState:
    session_id: str
    cwd: str
    project_root: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    plan_mode: bool = False
    todos: list[TodoItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'session_id': self.session_id,
            'cwd': self.cwd,
            'project_root': self.project_root,
            'model': self.model,
            'reasoning_effort': self.reasoning_effort,
            'plan_mode': self.plan_mode,
            'todos': [todo.to_dict() for todo in self.todos],
        }


class Session:
    """Runtime aggregate for one prime session."""

    def __init__(
        self,
        *,
        state: SessionState,
        ctx: Any,
        tracker: ResponseTracker,
        writer: SessionWriter,
    ) -> None:
        self.state = state
        self.ctx = ctx
        self.tracker = tracker
        self.writer = writer

    @property
    def session_id(self) -> str:
        return self.state.session_id

    def update_todos(self, todos: list[TodoItem]) -> tuple[list[TodoItem], list[TodoItem]]:
        old_todos = list(self.state.todos)
        stored_todos = [] if todos and all(todo.status == 'completed' for todo in todos) else list(todos)
        self.state.todos = stored_todos
        self.writer.write_todo_update(
            [todo.to_dict() for todo in old_todos],
            [todo.to_dict() for todo in todos],
        )
        self.writer.write_state_snapshot(self.state.to_dict())
        return old_todos, todos


class SessionStore:
    """In-process registry of live Session objects, backed by the sqlite Store.

    open() replays the (agent_id, session_id) event log from the Store into a
    Conversation (without re-writing history) and caches the Session so tools
    (TodoWrite) can reach it by session_id.
    """

    _sessions: dict[str, Session] = {}

    @classmethod
    def open(
        cls,
        *,
        session_id: str,
        agent_id: str,
        cwd: str | None = None,
        project_root: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        plan_mode: bool = False,
        max_items: int | None = 80,
    ) -> Session:
        from .store import get_store

        cwd_value = cwd or project_root or ''
        store = get_store()
        state, items, response_state = store.replay(
            agent_id, session_id,
            cwd=cwd_value,
            project_root=project_root,
            model=model,
            reasoning_effort=reasoning_effort,
            plan_mode=plan_mode,
        )
        writer = SessionWriter(
            session_id,
            agent_id=agent_id,
            cwd=state.cwd,
            model=state.model,
            plan_mode=state.plan_mode,
        )
        tracker = ResponseTracker()
        tracker.load(response_state)
        # ctx 由 agent 在 _init_session 里构造 (LocalContextProvider).
        # 这里先返回 None, agent 会 set.
        session = Session(state=state, ctx=None, tracker=tracker, writer=writer)
        cls._sessions[session_id] = session
        # 暂存 replay items 供 agent 构造 ctx 时 load_items
        session._replay_items = items  # type: ignore[attr-defined]
        session._max_items = max_items  # type: ignore[attr-defined]
        return session

    @classmethod
    def get(cls, session_id: str) -> Session | None:
        return cls._sessions.get(session_id)

    @classmethod
    def close(cls, session_id: str) -> None:
        cls._sessions.pop(session_id, None)


def _apply_state_snapshot(state: SessionState, snapshot: Any) -> None:
    if not isinstance(snapshot, dict):
        return
    state.cwd = str(snapshot.get('cwd') or state.cwd)
    state.project_root = snapshot.get('project_root') or state.project_root
    state.model = snapshot.get('model') or state.model
    # reasoning_effort: snapshot 中有该 key 才覆盖 (允许 None 显式关闭 reasoning)
    if 'reasoning_effort' in snapshot:
        effort = snapshot.get('reasoning_effort')
        state.reasoning_effort = str(effort) if effort else None
    state.plan_mode = bool(snapshot.get('plan_mode', state.plan_mode))
    state.todos = _parse_todos(snapshot.get('todos') or [])


def _parse_todos(value: Any) -> list[TodoItem]:
    if not isinstance(value, list):
        return []
    todos: list[TodoItem] = []
    for item in value:
        if isinstance(item, dict):
            todo = TodoItem.from_dict(item)
            if todo.content:
                todos.append(todo)
    return todos
