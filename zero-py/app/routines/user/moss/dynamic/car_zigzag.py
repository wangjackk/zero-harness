from typing import Any, ClassVar, Dict
from pydantic import BaseModel, Field
from routine import Routine


class CarZigzagInput(BaseModel):
    steps: int = Field(default=3, ge=1, le=8, description='走几段之字')
    width: float = Field(default=0.2, ge=0.05, le=0.6, description='左右横移宽度, 米')
    length: float = Field(default=0.3, ge=0.05, le=1.0, description='每段前进距离, 米')
    speed: float = Field(default=0.1, ge=0.02, le=0.3, description='速度 m/s')


class CarZigzag(Routine):
    name = 'car_zigzag'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '走之字形: 左移→前进, 右移→前进, 交替向前蛇行',
        'input_schema': CarZigzagInput.model_json_schema(),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stopped = False

    async def run(self, kwargs: Dict[str, Any]):
        inp = CarZigzagInput.model_validate(kwargs)
        side_dur = round(inp.width / inp.speed, 3)
        fwd_dur = round(inp.length / inp.speed, 3)
        done = 0
        try:
            for i in range(inp.steps):
                if self._stopped:
                    break
                action = 'left' if i % 2 == 0 else 'right'
                await self.call('car/move', {'action': action,
                                             'speed': inp.speed,
                                             'duration': side_dur})
                if self._stopped:
                    break
                await self.call('car/move', {'action': 'forward',
                                             'speed': inp.speed,
                                             'duration': fwd_dur})
                done += 1
        except Exception as exc:
            try:
                await self.call('car/stop', {})
            except Exception:
                pass
            return {'content': f'蛇行到第 {done + 1} 段出错, 已急停: {exc}'}
        try:
            await self.call('car/stop', {})
        except Exception:
            pass
        if self._stopped:
            return {'content': f'已中断, 走了 {done} 段'}
        return {'content': f'之字形走完 {done} 段, 已停'}

    async def stop(self) -> None:
        self._stopped = True
        try:
            await self.call('car/stop', {})
        except Exception:
            pass
