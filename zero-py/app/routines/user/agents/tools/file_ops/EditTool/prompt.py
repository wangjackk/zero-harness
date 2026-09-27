EDIT_TOOL_NAME = 'Edit'

DESCRIPTION = (
            'Performs exact string replacements in files. '
            'You MUST use the Read tool at least once before editing -- '
            'this tool will error if you attempt an edit without reading the file first. '
            'When editing text from Read tool output, ensure you preserve the exact indentation '
            '(tabs/spaces) as it appears after the line number prefix. '
            'ALWAYS prefer editing existing files. NEVER write new files unless explicitly required. '
            'The edit will FAIL if old_string is not unique in the file -- either provide a larger '
            'string with more surrounding context, or use replace_all to change every instance. '
            'Use replace_all for replacing and renaming strings across the file.'
        )
