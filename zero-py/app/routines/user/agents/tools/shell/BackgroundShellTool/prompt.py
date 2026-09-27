BACKGROUND_SHELL_TOOL_NAME = 'BackgroundShell'

DESCRIPTION = (
    'Spawn long-running commands in the background and inspect output later. '
    'Use for daemons/servers/watchers that do not exit on their own (e.g. starting one process). '
    'Actions: start (returns task_id, stdout+stderr pumped to .bg/<task_id>.log), '
    'status (tail recent lines + running/exited state), stop (kill), list. '
    'For short commands that exit quickly, prefer Bash instead.'
)
