READ_TOOL_NAME = 'Read'

DESCRIPTION = (
            'Reads a file from the local filesystem. You can access any file directly by using this tool. '
            'The file_path parameter must be an absolute path, not a relative path. '
            'You must explicitly specify limit to control context usage. '
            'Use offset and limit to read a focused line range. Positive offset is 1-based; negative offset counts backward from EOF (-1 is the last line). '
            'Pass limit=0 only when you intentionally need to read to EOF from the chosen offset. '
            'Results are returned using cat -n format, with line numbers starting at 1. '
            'ALWAYS use this tool to read files. NEVER use Bash(cat/head/tail).'
        )
