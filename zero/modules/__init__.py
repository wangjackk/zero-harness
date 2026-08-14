from typing import List

MODULE_OUTPUT = 'output'
MODULE_UI = 'ui'
MODULE_AUDIO = 'audio'
MODULE_BODY = 'body'
MODULE_MOUTH = 'mouth'

def get_modules() -> List[str]:
    return [MODULE_OUTPUT, MODULE_UI, MODULE_AUDIO, MODULE_BODY, MODULE_MOUTH]