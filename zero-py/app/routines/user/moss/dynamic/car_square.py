from typing import Any, ClassVar, Dict
from pydantic import BaseModel, Field
from routine import Routine


class CarSquareInput(BaseModel):
    side: float = Field(default=0.5, ge=0.1, le=3.0, description='正方形边长, 米')
    speed: float = Field(default=0.1, ge=0.02, le=0.3, description='平移速度 m/s')
    turn_rate: float = Field(default=0.5, ge=0.1, le=2.0, description='转向角速度 rad/s, 正=原地左转')
    laps: int = Field(default=1, ge=1, le=5, description='走几圈')


class CarSquare(Routine):
    name = 'car_square'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '让小车走正方形闭环: 走边长→左转90度, 重复四条边回到原点',
        'input_schema': CarSquareInput.model_json_schema(),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stopped = False

    async def run(self, kwargs: Dict[str, Any]):
        inp = CarSquareInput.model_validate(kwargs)
        move_dur = round(inp.side / inp.speed, 3)
        turn_dur = round((3.141592653589793 / 2) / inp.turn_rate, 3)
        done_edges = 0
        try:
            for lap in range(inp.laps):
                for edge in range(4):
                    if self._stopped:
                        break
                    await self.call('car/move', {'action': 'forward',
                                                 'speed': inp.speed,
                                                 'duration': move_dur})
                    if self._stopped:
                        break
                    await self.call('car/turn', {'turn_rate': inp.turn_rate,
                                                 'duration': turn_dur})
                    done_edges += 1
                if self._stopped:
                    break
        except Exception as exc:
            try:
                await self.call('car/stop', {})
            except Exception:
                pass
            return {'content': f'走到第 {done_edges + 1} 步出错, 已急停: {exc}'}
        try:
            await self.call('car/stop', {})
        except Exception:
            pass
        if self._stopped:
            return {'content': f'已中断, 走了 {done_edges} 条边后急停'}
        return {'content': f'走完 {inp.laps} 圈正方形(边长 {inp.side}m), 回到原点附近, 已停'}

    async def stop(self) -> None:
        self._stopped = True
        try:
            await self.call('car/stop', {})
        except Exception:
            pass
