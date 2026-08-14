"""TodoWrite ---- manage the current session task checklist."""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Literal

from pydantic import BaseModel, Field, model_validator
from routine import Routine

from ...._core.session import SessionStore, TodoItem
from zero.routines.user.agents._core.paths import AGENT_ID_KEY
from .prompt import DESCRIPTION


class TodoInputItem(BaseModel):
    content: str = Field(description='Imperative task description, e.g. "Run tests".')
    activeForm: str | None = Field(
        None,
        description='Present continuous form shown while running, e.g. "Running tests".',
    )
    status: Literal['pending', 'in_progress', 'completed'] = Field(
        description='Current task status.',
    )


class TodoWriteInput(BaseModel):
    todos: list[TodoInputItem] = Field(description='The updated todo list for the current session.')

    @model_validator(mode='after')
    def validate_in_progress_count(self) -> 'TodoWriteInput':
        in_progress = [todo for todo in self.todos if todo.status == 'in_progress']
        if len(in_progress) > 1:
            raise ValueError('Only one todo can be in_progress at a time.')
        return self


class TodoWriteOutput(BaseModel):
    oldTodos: list[TodoInputItem] = Field(description='The todo list before the update.')
    newTodos: list[TodoInputItem] = Field(description='The todo list after the update.')


class TodoWrite(Routine):
    """Update the task list stored on the current agent session."""

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': False,
        'input_schema': TodoWriteInput.model_json_schema(),
        'output_schema': TodoWriteOutput.model_json_schema(),
        'description': DESCRIPTION,
    }

    async def run(self, kwargs: Dict[str, Any]) -> dict[str, Any]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('fetch_agent_state', {'agent_id': agent_id})
        session_id = state.get('session_id') or ''
        if not session_id:
            raise RuntimeError('TodoWrite requires an active session_id.')

        session = SessionStore.get(session_id)
        if session is None:
            raise RuntimeError(f'Session not found for TodoWrite: {session_id}')

        inp = TodoWriteInput(**kwargs)
        todos = [
            TodoItem(
                content=todo.content,
                status=todo.status,
                active_form=todo.activeForm,
            )
            for todo in inp.todos
        ]
        old_todos, new_todos = session.update_todos(todos)

        return TodoWriteOutput(
            oldTodos=[_todo_to_tool_dict(todo) for todo in old_todos],
            newTodos=[_todo_to_tool_dict(todo) for todo in new_todos],
        ).model_dump(exclude_none=True)


def _todo_to_tool_dict(todo: TodoItem) -> dict[str, Any]:
    result: dict[str, Any] = {
        'content': todo.content,
        'status': todo.status,
    }
    if todo.active_form:
        result['activeForm'] = todo.active_form
    return result
