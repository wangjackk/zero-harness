from typing import List

# 模块树 id 常量 (对齐 app/tree.json: root -> ui/car(wheels, voice_modules(music/sfx/mouth/ear)))
MODULE_UI = 'ui'
MODULE_CAR = 'car'
MODULE_WHEELS = 'wheels'
MODULE_VOICE = 'voice_modules'
MODULE_MUSIC = 'audio_music'
MODULE_SFX = 'audio_sfx'
MODULE_MOUTH = 'audio_mouth'
MODULE_EAR = 'ear'

def get_modules() -> List[str]:
    return [MODULE_UI, MODULE_CAR, MODULE_WHEELS, MODULE_VOICE,
            MODULE_MUSIC, MODULE_SFX, MODULE_MOUTH, MODULE_EAR]
