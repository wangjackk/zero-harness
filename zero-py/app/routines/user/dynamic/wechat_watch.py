# -*- coding: utf-8 -*-
"""wechat_watch ---- 常驻监视微信, 一有"新收到"的消息就抓下内容和来源.

passive routine: 注册后 kernel 自动 start, 常驻轮询(默认 2s), 无需谁手动拉起.

它做什么(全无输入/焦点副作用):
  1. 每 interval 秒用 _wechat_probe.probe(target): PrintWindow 抓微信窗口(被盖住也能抓,
     不抢焦点) -> 左栏红点角标(像素) + 会话列表(OneOCR);
  2. 目标会话"从无未读 -> 有未读" = 来了新消息(自己发出去的消息不产生未读, 天然不误报);
  3. 取内容 = 该会话预览文本; 追加到 inbox 文件 + publish('wechat.message') + 记日志;
  4. 外部查询: @request('recent') / @request('probe') / @request('config').

inbox: <zero-py>/runtime/wechat_inbox.jsonl (每行一条 {ts, from, content}).
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, Field

from routine import Routine, request

from ._wechat_probe import probe as _probe

# app/routines/user/dynamic/wechat_watch.py -> parents[4] = zero-py/
_INBOX = Path(__file__).resolve().parents[4] / 'runtime' / 'wechat_inbox.jsonl'
_STATUS = Path(__file__).resolve().parents[4] / 'runtime' / 'wechat_watch.json'
_CFG = Path(__file__).resolve().parents[4] / 'runtime' / 'wechat_watch.cfg.json'


def _load_cfg() -> dict:
    try:
        if _CFG.is_file():
            return json.loads(_CFG.read_text(encoding='utf-8')) or {}
    except Exception:
        pass
    return {}


class WeChatWatchInput(BaseModel):
    target: str = Field(default='前行', description='要监视的微信联系人显示名')
    interval: float = Field(default=2.0, gt=0.2, description='轮询间隔秒')
    agent: Optional[str] = Field(
        default=None,
        description='命中时用 send_message 推送到的 agent_id (如 prime_1/pi)。不填 = 只落 inbox(静默)')


class WeChatWatch(Routine):
    """常驻监视微信新消息(被动启动, 自动轮询)."""

    name = 'wechat_watch'
    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '常驻监视微信: 目标联系人"从无未读变有未读"即判定收到新消息, 取预览内容写入 '
            'runtime/wechat_inbox.jsonl; 若配了 agent, 再 send_message 推给该 agent(点对点, '
            'agent 无需订阅任何渠道). PrintWindow 抓窗口, 不抢焦点/不模拟输入. '
            'passive routine, 注册即自动运行.'),
        'input_schema': WeChatWatchInput.model_json_schema(),
    }

    def __init__(self):
        super().__init__()
        self._stop = asyncio.Event()
        self._target = '前行'
        self._interval = 2.0
        self._agent = None
        self._owner_since = None
        self._recent = []          # 最近事件(内存环)
        self._inited = False
        self._had_badge = False
        self._last_preview = None
        self._polls = 0
        self._hits = 0

    async def run(self, kwargs: Dict[str, Any]):
        # 配置持久化: 热重载会新建实例, 不落盘的话 req 设的 agent 会丢.
        # 优先级: kwargs > 持久化配置 > 默认值
        cfg = _load_cfg()
        merged = {k: v for k, v in cfg.items() if v is not None}
        merged.update({k: v for k, v in (kwargs or {}).items() if v is not None})
        try:
            inp = WeChatWatchInput.model_validate(merged)
        except Exception:
            inp = WeChatWatchInput()
        self._target = inp.target
        self._interval = inp.interval
        self._set_agent(inp.agent)
        self._save_cfg()
        self._logger.info('wechat_watch: start (target=%s interval=%.1fs inbox=%s)',
                          self._target, self._interval, _INBOX)
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except Exception as exc:  # 单帧失败不致命
                self._logger.warning('wechat_watch: poll error: %r', exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass
        self._logger.info('wechat_watch: stopped (polls=%d hits=%d)', self._polls, self._hits)

    async def stop(self):
        self._stop.set()

    async def _poll_once(self):
        st = await asyncio.to_thread(_probe, self._target)
        self._polls += 1
        if not st.get('ok'):
            return
        row = st.get('row')
        if row is None:
            # 看不到该行(被挤出列表/OCR 漏) -> 不改状态, 避免"假的无未读"造成重复上报
            return
        has_badge = bool(row.get('badge'))
        preview = (row.get('preview') or '').strip()
        self._write_status(row, has_badge, preview, st)
        if not self._inited:
            self._inited = True
            self._had_badge = has_badge
            self._last_preview = preview
            self._logger.info('wechat_watch: baseline target=%s badge=%s preview=%r',
                              self._target, has_badge, preview)
            # 启动时若已经压着未读(比如最小化期间来过了), 也补报一条;
            # 但 inbox 最近已有同内容就不重复报(否则每次热重载都会重报)
            if has_badge and preview and not self._recently_reported(preview):
                await self._emit(preview, baseline=True)
            return
        # 触发: 有未读 且 (刚变未读 或 预览文本变了) -> 覆盖"未读从1涨到2"这种连续来消息
        if has_badge and preview and (not self._had_badge or preview != self._last_preview):
            await self._emit(preview)
        self._had_badge = has_badge
        self._last_preview = preview

    def _save_cfg(self):
        try:
            _CFG.parent.mkdir(parents=True, exist_ok=True)
            _CFG.write_text(json.dumps({'target': self._target,
                                        'interval': self._interval,
                                        'agent': self._agent}, ensure_ascii=False),
                            encoding='utf-8')
        except Exception:
            pass

    def _recently_reported(self, content, window=600.0):
        try:
            if not _INBOX.is_file():
                return False
            now = time.time()
            for ln in reversed(_INBOX.read_text(encoding='utf-8').splitlines()[-30:]):
                try:
                    ev = json.loads(ln)
                except Exception:
                    continue
                if ev.get('content') == content and (now - float(ev.get('ts', 0))) < window:
                    return True
        except Exception:
            pass
        return False

    def _write_status(self, row, has_badge, preview, st):
        try:
            _STATUS.parent.mkdir(parents=True, exist_ok=True)
            _STATUS.write_text(json.dumps({
                'ts': time.time(), 'iso': time.strftime('%Y-%m-%d %H:%M:%S'),
                'target': self._target, 'polls': self._polls, 'hits': self._hits,
                'row_y': row.get('y'), 'badge': has_badge, 'preview': preview,
                'iconic': st.get('iconic'),
            }, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass

    async def _emit(self, preview, baseline=False):
        self._hits += 1
        ev = {'ts': time.time(), 'iso': time.strftime('%Y-%m-%d %H:%M:%S'),
              'from': self._target, 'content': preview, 'source': 'preview',
              'baseline': baseline}
        self._recent.append(ev)
        self._recent = self._recent[-50:]
        self._logger.info('wechat_watch: NEW from %s -> %r (baseline=%s)',
                          self._target, preview, baseline)
        try:
            _INBOX.parent.mkdir(parents=True, exist_ok=True)
            with _INBOX.open('a', encoding='utf-8') as f:
                f.write(json.dumps(ev, ensure_ascii=False) + '\n')
        except Exception as exc:
            self._logger.warning('wechat_watch: inbox write failed: %r', exc)
        try:
            await self.publish('wechat.message', ev)
        except Exception as exc:
            self._logger.warning('wechat_watch: publish failed: %r', exc)
        # 点对点推给指定 agent(agent 不需要订阅渠道); 没配 agent 就静默
        if self._agent:
            try:
                r = await self.call('send_message', kwargs={
                    'to': self._agent,
                    'from': 'wechat_watch',
                    'content': [{'type': 'text',
                                 'text': '[微信/%s] %s' % (self._target, preview)}],
                })
                self._logger.info('wechat_watch: notified agent %s -> %s', self._agent, r)
            except Exception as exc:
                self._logger.warning('wechat_watch: send_message failed: %r', exc)

    # --- 外部查询/配置 ---

    # --- 外部查询/配置 (@request handler 约定签名: (self, source, data)) ---

    @request('recent')
    async def _req_recent(self, source, data):
        limit = int((data or {}).get('limit', 10))
        return {'target': self._target, 'polls': self._polls, 'hits': self._hits,
                'events': self._recent[-limit:]}

    @request('probe')
    async def _req_probe(self, source, data):
        return await asyncio.to_thread(_probe, self._target)

    @request('config')
    async def _req_config(self, source, data):
        d = data or {}
        if d.get('target'):
            self._target = str(d['target'])
            self._inited = False
        if d.get('interval'):
            self._interval = float(d['interval'])
        if 'agent' in d:
            self._set_agent(d['agent'])
        self._save_cfg()
        return self._status_dict()

    @request('set_agent')
    async def _req_set_agent(self, source, data):
        """动态绑定/解绑推送目标 agent.

        另一个 routine 拿到本实例 rid 后 req 进来即可::

            routines = await self.get_running_routines()
            rid = next(r['id'] for r in routines if r['name'] == 'wechat_watch')
            await self.req(rid, 'set_agent', {'agent': 'pi_1'})   # 绑定
            await self.req(rid, 'set_agent', {'agent': None})     # 解绑(=静默)
        """
        agent = (data or {}).get('agent')
        self._set_agent(agent)
        self._save_cfg()
        self._logger.info('wechat_watch: set_agent -> %s', self._agent)
        return {'ok': True, **self._status_dict()}

    @request('status')
    async def _req_status(self, source, data):
        return self._status_dict()

    def _set_agent(self, agent):
        """统一入口: 设/清 owner, 记录 '从什么时候开始用'."""
        new = (str(agent).strip() or None) if agent else None
        if new != self._agent:
            self._agent = new
            self._owner_since = time.time() if new else None
        return self._agent

    def _status_dict(self):
        return {'target': self._target, 'interval': self._interval,
                'agent': self._agent, 'owner': self._agent,
                'owner_since': (time.strftime('%Y-%m-%d %H:%M:%S',
                                              time.localtime(self._owner_since))
                                if self._owner_since else None),
                'polls': self._polls, 'hits': self._hits,
                'inited': self._inited}


__all__ = ['WeChatWatch', 'WeChatWatchInput']
