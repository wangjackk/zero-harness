from typing import Any, ClassVar, Dict
from pydantic import BaseModel, Field
from routine import Routine


class CarDanceInput(BaseModel):
    cycles: int = Field(default=3, ge=1, le=8, description='摇头来回几次')
    turns: int = Field(default=2, ge=0, le=6, description='原地转几下(每下90度)')
    speed: float = Field(default=0.1, ge=0.02, le=0.3, description='收尾前进速度 m/s')
    advance: float = Field(default=0.3, ge=0.0, le=2.0, description='收尾前进距离, 米')


class CarDance(Routine):
    name = 'car_dance'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '开场舞: 先摇头, 再原地转圈, 最后前进一小段谢幕',
        'input_schema': CarDanceInput.model_json_schema(),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stopped = False

    async def run(self, kwargs: Dict[str, Any]):
        inp = CarDanceInput.model_validate(kwargs)
        steps = []
        try:
            await self.call('car/shake', {'mode': 'yaw', 'freq': 2.0,
                                          'amp': 1.5, 'cycles': inp.cycles})
            steps.append(f'摇头{inp.cycles}次')
            for i in range(inp.turns):
                if self._stopped:
                    break
                await self.call('car/turn', {'turn_rate': 0.8, 'duration': 1.963})
                steps.append('转90度')
            if inp.advance > 0 and not self._stopped:
                await self.call('car/move', {'action': 'forward',
                                             'speed': inp.speed,
                                             'duration': round(inp.advance / inp.speed, 3)})
                steps.append(f'前进{inp.advance}m')
        except Exception as exc:
            try:
                await self.call('car/stop', {})
            except Exception:
                pass
            return {'content': f'跳舞出错, 已急停: {exc} 已完成: {"-".join(steps) or "无"}'}
        try:
            await self.call('car/stop', {})
        except Exception:
            pass
        if self._stopped:
            return {'content': f'已中断, 完成: {"-".join(steps) or "无"}'}
        return {'content': '开场舞跳完了: ' + ' → '.join(steps) + ', 已停'}

    async def stop(self) -> None:
        self._stopped = True
        try:
            await self.call('car/stop', {})
        except Exception:
            pass
