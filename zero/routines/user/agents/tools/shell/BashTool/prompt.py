BASH_TOOL_NAME = 'Bash'

DESCRIPTION = (
            'Execute a bash command and return combined stdout + stderr. '
            'Use Unix syntax (not CMD): forward slashes, && to chain commands, /dev/null not NUL. '
            'Use only when Read/Grep/Glob/Edit cannot accomplish the task. '
            'Non-readonly commands require user approval before execution.'
        )
