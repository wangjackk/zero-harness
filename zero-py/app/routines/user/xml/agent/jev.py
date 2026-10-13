"""JevClient ---- decisions 异步客户端(协议层, 不含业务问题).

Noul(是否概率) / Choice(选项) / Score(有序档位), 统一走
POST /v1/systemone: {state, questions} -> answers, 按题名返回
noul/score + confidence.

后端是自部署的 decider(Mapika/decider-2b, localhost:8300, 免鉴权);
协议兼容 OpenRouter decisions; 本模块只管 httpx 异步连接(keep-alive)/
超时/answer 解析. 问题组装与决策语义在消费方(如 reflex.py 的插话判断);
Jev 失败的降级也归消费方(reflex: fallback interrupt, 宁打断不丢消息).
"""
from __future__ import annotations

from typing import Any

import httpx

_API = 'http://127.0.0.1:8300/v1/systemone'
# localhost 在 Windows 上先探 IPv6 ::1(服务只绑 127.0.0.1), 卡 2s 再回退 ---- 必须写 127.0.0.1
_DEFAULT_TIMEOUT_S = 8.0


class JevClient:
    """异步 decisions 客户端: 实例长期持有(keep-alive), stop 时 aclose()."""

    def __init__(self, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def decide(self, state: dict[str, Any],
                     questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """通用原语: 按题名组装 questions -> 按题名 answers(原样字典).

        一个请求多题是推荐形态 ---- 共享同一 state, 一次往返出全部判断.
        """
        resp = await self._http.post(
            _API,
            json={'state': state, 'questions': questions},
        )
        resp.raise_for_status()
        return resp.json()['answers']

    async def noul(self, state: dict[str, Any], instructions: str,
                   criteria: dict[str, str]) -> tuple[float, float]:
        """单题便捷方法: 是否概率 -> (p, confidence)."""
        answers = await self.decide(state, {'q': {
            'type': 'noul',
            'instructions': instructions,
            'criteria': criteria,
        }})
        a = answers['q']
        return float(a['noul']), float(a.get('confidence', 0.0))

    async def score(self, state: dict[str, Any], instructions: str,
                    criteria: list[str]) -> tuple[float, float]:
        """单题便捷方法: 有序档位 -> (档位位置, confidence).

        criteria 为低->高的有序档位表(如情绪 valence 5 档); 消费方用
        (pos - 中点) * 步长 折增量(如 (pos-2)*0.25).
        """
        answers = await self.decide(state, {'q': {
            'type': 'score',
            'instructions': instructions,
            'criteria': criteria,
        }})
        a = answers['q']
        return float(a['score']), float(a.get('confidence', 0.0))

    async def aclose(self) -> None:
        """停机收尾: 关连接池. 在途请求会被掐断, 调用方先 cancel 依赖方."""
        await self._http.aclose()
