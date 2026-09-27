RUN_ROUTINE_TOOL_NAME = 'RunRoutine'

DESCRIPTION = (
    'Invoke routine by name with the given kwargs, returning the '
    "routine's result. "
    '`name` must match a registered routine name; `kwargs` is the routine input '
    'dict (must match its input_schema if declared). Returns the routine result, '
    'or {error: ...} on failure (not found / raised / start rejected).'
)
