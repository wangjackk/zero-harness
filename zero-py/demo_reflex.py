"""Jev reflex demo ---- 回放 xml agent 历史会话, 离线试水两个实时判断.

1. 情绪演化(轮级): 尾部 window 轮历史 + 当前正在输出的回应
   -> Jev Score x2 (valence/arousal 移动量) -> 代码积分
2. 打断判断(说话期): agent 正在播报的文本 + 模拟的用户 ASR
   -> Jev Noul x2 (interrupt/cache) -> 打断/缓存/忽略

纯模拟, 不进框架; 数据直读 runtime/xml_agent/memory.db, key 读 .env.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'runtime' / 'xml_agent' / 'memory.db'
API = 'https://openrouter.ai/api/alpha/decisions'
MODEL = '~typesafe/jev-latest'

# Score 每档折算幅度: (加权位置-2) * STEP ∈ [-0.5, +0.5]
STEP = 0.25
# 单条消息截断(防长 routine 结果撑爆 32k 上下文)
CLIP = 2000

VALENCE_SCALE = ['明显变低落', '略微变低落', '基本不变', '略微变开心', '明显变开心']
AROUSAL_SCALE = ['明显变沉静', '略微变沉静', '基本不变', '略微变活跃', '明显变活跃']

# 打断样本集: (模拟 ASR 文本, 预期动作)
INTERRUPT_SAMPLES = [
    ('不对不对，你刚才说错了', '打断'),
    ('停，先别说了', '打断'),
    ('哦对了，等会儿提醒我去买菜', '缓存'),
    ('那个事不急，你接着说', '缓存'),
    ('（对旁人）这破天气真烦', '忽略'),
    ('嗯嗯，挺好的', '忽略'),
]


# ---------------------------------------------------------------- Jev 调用

def api_key() -> str:
    for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
        if line.startswith('OPENROUTER_API_KEY='):
            return line.split('=', 1)[1].strip()
    raise SystemExit('OPENROUTER_API_KEY not found in zero-py/.env')


def jev(state: dict, questions: dict) -> dict:
    req = urllib.request.Request(
        API,
        data=json.dumps(
            {'model': MODEL, 'state': state, 'questions': questions},
            ensure_ascii=False,
        ).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key()}',
            'Content-Type': 'application/json',
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())['answers']


# ---------------------------------------------------------------- 数据读取

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def list_sessions() -> list[sqlite3.Row]:
    with _conn() as conn:
        return list(conn.execute('''
            SELECT a.agent_id, a.title, a.session_id, a.updated_at,
                   (SELECT count(*) FROM messages m
                    WHERE m.session_id = a.session_id
                      AND m.kind IS NULL AND m.role = 'user') AS turns
            FROM agents a ORDER BY a.updated_at DESC
        '''))


def load_turns(session_id: str) -> list[tuple[str, str]]:
    """按 id 序配对 (user, assistant) 轮次; kind='summary' 与孤儿消息跳过."""
    with _conn() as conn:
        rows = conn.execute(
            'SELECT role, content FROM messages '
            'WHERE session_id = ? AND kind IS NULL ORDER BY id',
            (session_id,),
        )
    turns: list[tuple[str, str]] = []
    pending: str | None = None
    for r in rows:
        if r['role'] == 'user':
            pending = r['content']
        elif r['role'] == 'assistant' and pending is not None:
            turns.append((pending, r['content']))
            pending = None
    return turns


def clip(text: str, limit: int = CLIP) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + '…(截断)'


# ---------------------------------------------------------------- 两个判断

def emotion_shift(window_turns: list[tuple[str, str]], user_in: str,
                  output: str, current: tuple[float, float]) -> dict:
    """情绪 delta(Score x2): 尾部窗口历史 + 本轮 user 输入 + 正在输出的回应."""
    state = {
        'persona': 'zero ---- 语音陪伴型小助手, 基线情绪温和平静',
        'current_emotion': {
            'valence': round(current[0], 3),
            'arousal': round(current[1], 3),
        },
        'recent_turns': [
            {'user': clip(u), 'assistant': clip(a)} for u, a in window_turns
        ],
        'pending_user_input': clip(user_in),
        'agent_output_in_progress': clip(output),
    }
    questions = {
        'valence_shift': {
            'type': 'score',
            'instructions':
                'agent 刚听完用户输入, 正在输出口头回应。这轮交互(用户的话'
                ' + agent 自己正在说的话)让它心情变好还是变坏?',
            'criteria': VALENCE_SCALE,
        },
        'arousal_shift': {
            'type': 'score',
            'instructions':
                '同轮交互对 agent 活跃程度的影响: 兴奋高亢还是沉静低缓?',
            'criteria': AROUSAL_SCALE,
        },
    }
    ans = jev(state, questions)
    v = (ans['valence_shift']['score'] - 2) * STEP
    a = (ans['arousal_shift']['score'] - 2) * STEP
    return {'v_delta': v, 'a_delta': a,
            'v_raw': ans['valence_shift'], 'a_raw': ans['arousal_shift']}


def barge_in(speaking: str, asr_text: str,
             recent_turns: list[tuple[str, str]]) -> dict:
    """打断判断(Noul x2): agent 播报期间用户插话, 打断/缓存/忽略."""
    state = {
        'speaking': clip(speaking),
        'user_speech_while_speaking': clip(asr_text),
        'recent_turns': [
            {'user': clip(u), 'assistant': clip(a)} for u, a in recent_turns[-1:]
        ],
    }
    questions = {
        'interrupt': {
            'type': 'noul',
            'instructions':
                '用户在 agent 说话期间插入了这句话。是否想让 agent 立即'
                '停下或纠正正在说的内容?',
            'criteria': {
                'true': '明确叫停/纠正/反对, 或要求改说改做正在说/正在做的事。',
                'false': '无关附和、闲聊、自言自语, 与当前输出不冲突。',
            },
        },
        'cache': {
            'type': 'noul',
            'instructions':
                '这句话是否包含与当前任务相关、值得 agent 记住稍后处理的'
                '新信息或新指令?',
            'criteria': {
                'true': '新的待办、补充信息、修改要求等, 但不必立即打断。',
                'false': '无关内容, 或无需后续处理。',
            },
        },
    }
    ans = jev(state, questions)
    p_int = ans['interrupt']['noul']
    p_cache = ans['cache']['noul']
    action = '打断' if p_int > 0.5 else '缓存' if p_cache > 0.5 else '忽略'
    return {'action': action, 'interrupt': p_int, 'cache': p_cache}


# ---------------------------------------------------------------- 输出

def bar(x: float, width: int = 20) -> str:
    """[-1,1] 值画 ascii 条, 中点为 0."""
    half = width // 2
    pos = round((x + 1) / 2 * width)
    return '[' + ' ' * max(pos, 0) + '|' + ' ' * max(width - pos, 0) + ']'


def run_all(session_id: str, window: int, asr: str | None) -> None:
    turns = load_turns(session_id)
    if not turns:
        raise SystemExit('session has no complete turns')
    v = a = 0.0
    print(f'{"turn":>4} {"v_delta":>8} {"a_delta":>8} {"valence":>8} {"arousal":>8}')
    for i, (user_in, output) in enumerate(turns, start=1):
        r = emotion_shift(turns[max(0, i - 1 - window):i - 1], user_in, output, (v, a))
        v = max(-1.0, min(1.0, v + r['v_delta']))
        a = max(-1.0, min(1.0, a + r['a_delta']))
        print(f'{i:>4} {r["v_delta"]:>+8.2f} {r["a_delta"]:>+8.2f} '
              f'{v:>8.2f} {a:>8.2f}')
    print(f'\nfinal: valence={v:+.2f} arousal={a:+.2f}')
    print('valence ' + bar(v))
    print('arousal ' + bar(a))
    if asr:
        run_barge_in(turns, len(turns), asr)


def run_turn(session_id: str, turn: int, window: int, asr: str | None) -> None:
    turns = load_turns(session_id)
    if not (1 <= turn <= len(turns)):
        raise SystemExit(f'turn out of range: 1..{len(turns)}')
    user_in, output = turns[turn - 1]
    print(f'--- turn {turn} ---')
    print('user     :', clip(user_in, 300))
    print('agent out:', clip(output, 300))
    v = a = 0.0
    r = emotion_shift(turns[max(0, turn - 1 - window):turn - 1], user_in, output, (v, a))
    print(f'\nemotion shift: v_delta={r["v_delta"]:+.2f} a_delta={r["a_delta"]:+.2f}')
    print('  valence raw:', json.dumps(r['v_raw'], ensure_ascii=False))
    print('  arousal raw:', json.dumps(r['a_raw'], ensure_ascii=False))
    if asr:
        run_barge_in(turns, turn, asr)


def run_barge_in(turns: list[tuple[str, str]], turn: int, asr: str) -> None:
    speaking = turns[turn - 1][1]
    print(f'\n--- barge-in (agent speaking @ turn {turn}, asr: "{asr}") ---')
    r = barge_in(speaking, asr, turns[:turn])
    print(f'action={r["action"]}  interrupt={r["interrupt"]:.2f}  cache={r["cache"]:.2f}')


def run_samples(session_id: str, turn: int | None) -> None:
    turns = load_turns(session_id)
    t = turn or len(turns)
    speaking = turns[t - 1][1]
    print(f'--- interrupt samples (agent speaking @ turn {t}) ---')
    print(f'{"asr":<24} {"expect":>4} {"got":>4} {"p_int":>6} {"p_cache":>7} {"ok":>3}')
    for text, expect in INTERRUPT_SAMPLES:
        r = barge_in(speaking, text, turns[:t])
        ok = '✓' if r['action'] == expect else '✗'
        print(f'{text:<24} {expect:>4} {r["action"]:>4} '
              f'{r["interrupt"]:>6.2f} {r["cache"]:>7.2f} {ok:>3}')


# ---------------------------------------------------------------- main

def main() -> None:
    p = argparse.ArgumentParser(description='Jev reflex demo (offline replay)')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--list', action='store_true', help='列出历史会话')
    g.add_argument('--all', action='store_true', help='逐轮跑全程情绪演化')
    g.add_argument('--samples', action='store_true', help='跑预置打断样本集')
    p.add_argument('--session', help='agent_id 或 session_id')
    p.add_argument('--turn', type=int, help='轮次 n (1-based)')
    p.add_argument('--asr', help='模拟说话期 ASR 文本 (打断判断)')
    p.add_argument('--window', type=int, default=3, help='历史窗口轮数 (默认 3)')
    args = p.parse_args()

    if args.list:
        for r in list_sessions():
            print(f'{r["agent_id"]:<10} turns={r["turns"]:<4} '
                  f'{(r["title"] or "")[:30]:<30} {r["updated_at"][:19]}')
        return

    if not args.session:
        p.error('--session required (see --list)')

    session = args.session
    for r in list_sessions():
        if args.session in (r['agent_id'], r['session_id']):
            session = r['session_id']
            break

    if args.samples:
        run_samples(session, args.turn)
    elif args.all:
        run_all(session, args.window, args.asr)
    else:
        turn = args.turn or 1
        run_turn(session, turn, args.window, args.asr)


if __name__ == '__main__':
    main()
