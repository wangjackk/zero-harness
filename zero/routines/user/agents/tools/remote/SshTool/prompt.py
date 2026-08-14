SSH_TOOL_NAME = 'Ssh'

DESCRIPTION = (
    'Manage persistent SSH sessions via AsyncSSH. '
    'Use action=connect with an alias to open a real SSH connection and start a long-lived /bin/sh shell. '
    'action=exec runs commands inside that shell; pass alias to pick a specific connection, or omit to use the active one. '
    'action=upload/download transfers files or directories; action=disconnect closes a session by alias; action=list shows all. '
    'A single agent session can hold multiple concurrent SSH connections, each identified by its alias. '
    'Shell state (cd, export) persists across exec calls within the same connection. '
)
