PROMPT = """Use this tool to create and manage a structured task list for your current coding session.

Use it for non-trivial multi-step work, especially when there are 3 or more distinct steps,
when the user gives multiple tasks, or when tracking progress would make the work clearer.

Do not use it for simple one-step requests, purely conversational answers, or trivial commands.

Task states:
- pending: Task has not started.
- in_progress: Task currently being worked on. Keep at most one task in this state.
- completed: Task finished successfully.

Update the list as work progresses. Mark a task completed immediately after finishing it, then
mark the next task in_progress before starting it. Remove tasks that are no longer relevant.

Each task should include:
- content: Imperative form, e.g. "Run tests".
- activeForm: Present continuous form, e.g. "Running tests".
"""

DESCRIPTION = (
    'Update the todo list for the current session. Use proactively for complex multi-step tasks. '
    'Keep at most one task in_progress and update task status as work progresses.'
)
