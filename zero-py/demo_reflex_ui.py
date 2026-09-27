"""Jev reflex demo UI ---- FastAPI 单服务: API + 静态单页.

复用 demo_reflex 的 Jev 调用与数据读取; 运行: uv run python demo_reflex_ui.py
(浏览器打开 http://127.0.0.1:7788)
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

import demo_reflex as core

app = FastAPI(title='jev-reflex-demo')


def _resolve(agent: str) -> str:
    """agent_id / session_id -> session_id."""
    for r in core.list_sessions():
        if agent in (r['agent_id'], r['session_id']):
            return r['session_id']
    raise HTTPException(404, f'agent not found: {agent}')


@app.get('/')
def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / 'demo_reflex_ui.html')


@app.get('/api/sessions')
def sessions() -> list[dict]:
    return [dict(r) for r in core.list_sessions()]


@app.get('/api/turns')
def turns(agent: str) -> dict:
    ts = core.load_turns(_resolve(agent))
    return {'turns': [
        {'user': core.clip(u), 'assistant': core.clip(a)} for u, a in ts
    ]}


class EmotionReq(BaseModel):
    agent: str
    turn: int
    window: int = 3
    valence: float = 0.0
    arousal: float = 0.0


@app.post('/api/emotion')
def emotion(req: EmotionReq) -> dict:
    """单轮情绪判断: 返回 delta + raw 概率 + 积分后的新 valence/arousal."""
    ts = core.load_turns(_resolve(req.agent))
    if not (1 <= req.turn <= len(ts)):
        raise HTTPException(400, f'turn out of range: 1..{len(ts)}')
    user_in, output = ts[req.turn - 1]
    r = core.emotion_shift(
        ts[max(0, req.turn - 1 - req.window):req.turn - 1],
        user_in, output, (req.valence, req.arousal),
    )
    v = max(-1.0, min(1.0, req.valence + r['v_delta']))
    a = max(-1.0, min(1.0, req.arousal + r['a_delta']))
    return {**r, 'valence': v, 'arousal': a}


class BargeReq(BaseModel):
    agent: str
    turn: int
    asr: str


@app.post('/api/barge')
def barge(req: BargeReq) -> dict:
    """打断判断: agent 播报 turn 轮内容期间插入 req.asr."""
    ts = core.load_turns(_resolve(req.agent))
    if not (1 <= req.turn <= len(ts)):
        raise HTTPException(400, f'turn out of range: 1..{len(ts)}')
    return core.barge_in(ts[req.turn - 1][1], req.asr, ts[:req.turn])


if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=7788, log_level='warning')
