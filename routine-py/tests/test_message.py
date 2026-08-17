"""message.* 定向消息(push)单元测试.

in-process RoutineHub + kernel relay client(message.send→delivered + lifecycle.created 回环).
覆盖:created 后即可收 / on_message 派发 / 业务 id reorder(并发到达乱序处理).
"""
import asyncio
import json
import unittest

import grpc
from routine.protocol import dict_to_frame, frame_to_dict

from routine import Routine, Routines, RoutineHub, GrpcServerTransport
from routine.grpc import routine_pb2_grpc
from routine.protocol import (
    LIFECYCLE_CREATED, MESSAGE_DELIVERED, MESSAGE_SEND,
)

LIFECYCLE_START = 'lifecycle.start'
LIFECYCLE_STOP = 'lifecycle.stop'

# message.* send → delivered 配对(对标 kernel broker.OnMessage).
_MSG_DELIVERED = {MESSAGE_SEND: MESSAGE_DELIVERED}


class Receiver(Routine):
    """created 后即收 message;on_message 按业务 id reorder 后存 class-level received.

    业务自带 id:on_message 可能并发 fire,乱序到达,靠 next_expected 顺序消费----
    顺序不到的先暂存 pending,到了再处理.验证 reorder 逻辑.
    """

    received: list = []
    pending: dict = {}
    next_expected: int = 0
    _done: asyncio.Event = None  # type: ignore[assignment]

    async def on_created(self, rid, kwargs):
        # created 后即可收----重置状态
        Receiver.received = []
        Receiver.pending = {}
        Receiver.next_expected = 0
        Receiver._done = asyncio.Event()

    async def on_message(self, source, data):
        """收到一条消息:按 data['id'] reorder.id 顺序到了就 received.append,
        不到的暂存 pending.收齐 3 条 set _done."""
        if data is None:
            return
        seq = int(data.get('id', -1))
        Receiver.pending[seq] = data
        # 顺序消费:next_expected 到了就 append,推进
        while Receiver.next_expected in Receiver.pending:
            Receiver.received.append(Receiver.pending.pop(Receiver.next_expected))
            Receiver.next_expected += 1
        if len(Receiver.received) >= 3:
            Receiver._done.set()

    async def run(self, kwargs):
        # start 只等收齐 3 条(created 阶段就可能已收)----验 created 后即可收
        await Receiver._done.wait()
        return {'received': len(Receiver.received)}


class Sender(Routine):
    """submit Receiver(created 后即可 send),pre-start 投递 3 条(带 id),再 start."""

    async def run(self, kwargs):
        handle = await self.submit('receiver', {})
        # created 后即可发 message(不必 start)----投 3 条带 id
        for i in range(3):
            await self.send(handle.id, {'id': i, 'payload': f'msg{i}'})
        # 现在 start receiver,让它等收齐(created 阶段可能已收完)
        await handle.start()
        result = await handle.wait()
        return result


class _KernelRelayClient:
    """gRPC bidi Stream client,模拟 kernel:routine.submit→created+submitted,
    message.send→delivered,lifecycle.created 回声,lifecycle.start/stop,started/stopped 回声.

    为了验证 reorder,deliberately 乱序投递:把第 0 条延迟到第 2 条之后.
    """

    def __init__(self, addr):
        self.channel = grpc.aio.insecure_channel(addr)
        self.stub = routine_pb2_grpc.RoutineServiceStub(self.channel)
        self._call = self.stub.Stream()
        self.events: asyncio.Queue = asyncio.Queue()
        self._next_id = 10
        self._submit_meta: dict = {}
        self._pending_submits: dict = {}
        self._reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for msg in self._call:
                d = frame_to_dict(msg)
                ev = d.get('event')
                if ev == 'routine.submit':
                    self._next_id += 1
                    cid = str(self._next_id)
                    name, kwargs = d.get('name', ''), d.get('kwargs') or {}
                    self._submit_meta[cid] = (name, kwargs)
                    created = {'event': LIFECYCLE_CREATED, 'id': cid, 'name': name}
                    if kwargs:
                        created['kwargs'] = kwargs
                    await self._write(created)
                    self._pending_submits[cid] = d.get('req_id', '')
                elif ev == 'lifecycle.created':
                    cid = d.get('id', '')
                    req_id = self._pending_submits.pop(cid, None)
                    if req_id:
                        await self._write({'event': 'routine.submitted',
                                           'req_id': req_id, 'child_id': cid})
                elif ev == 'routine.start':
                    cid = d.get('child_id', '')
                    name, _ = self._submit_meta.get(cid, ('', {}))
                    p = {'event': LIFECYCLE_START, 'id': cid, 'name': name}
                    start_kwargs = d.get('kwargs')
                    if start_kwargs:
                        p['kwargs'] = start_kwargs
                    await self._write(p)
                elif ev == 'routine.stop':
                    await self._write({'event': LIFECYCLE_STOP, 'id': d.get('child_id', '')})
                elif ev in ('lifecycle.started', 'lifecycle.stopped'):
                    await self._write(d)
                    await self.events.put(d)
                elif ev == MESSAGE_SEND:
                    # 模拟乱序:把 id=0 的消息延迟投递,验证业务 reorder.
                    # frame_to_dict 后 data 已是 dict,直接读(对标真 kernel 只透传).
                    seq = int((d.get('data') or {}).get('id', -1))
                    if seq == 0:
                        # 延迟:先投后续,再投这条
                        asyncio.create_task(self._delayed_relay(d, delay=0.1))
                    else:
                        await self._relay(d)
                else:
                    await self.events.put(d)
        except Exception:
            pass

    async def _delayed_relay(self, msg, delay):
        await asyncio.sleep(delay)
        await self._relay(msg)

    async def _relay(self, msg):
        source_id = msg.get('source_id', '')
        data = msg.get('data')
        for tid in msg.get('target_ids', []):
            delivered = {'event': MESSAGE_DELIVERED, 'target_id': tid,
                         'source': {'id': source_id}}
            if data is not None:
                delivered['data'] = data
            await self._write(delivered)

    async def _write(self, d):
        s = dict_to_frame(d)
        await self._call.write(s)

    async def create(self, id, name, kwargs=None):
        d = {'event': LIFECYCLE_CREATED, 'id': id, 'name': name}
        if kwargs:
            d['kwargs'] = kwargs
        await self._write(d)

    async def start(self, id, name):
        d = {'event': LIFECYCLE_START, 'id': id, 'name': name}
        await self._write(d)

    async def recv(self, predicate=None, timeout=5.0):
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError('recv timeout')
            msg = await asyncio.wait_for(self.events.get(), timeout=remaining)
            if predicate is None or predicate(msg):
                return msg

    async def close(self):
        self._reader.cancel()
        try:
            await self._call.done_writing()
        except Exception:
            pass
        await self.channel.close()


class TestMessage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rs = Routines()
        self.rs.register(Receiver, Sender)
        self.transport = GrpcServerTransport('127.0.0.1:0')
        self.server = RoutineHub(self.rs, transport=self.transport, hub_id='t')
        self.transport.attach(self.server)
        await self.transport.start()
        self.client = _KernelRelayClient(f'127.0.0.1:{self.transport.bound_port}')

    async def asyncTearDown(self):
        try:
            await self.client.close()
        except Exception:
            pass
        await self.transport.stop()

    async def test_message_push_and_reorder(self):
        """Sender submit Receiver → send 3 条(created 后即可,pre-start,id=0 延迟到达)
        → Receiver on_message 按业务 id reorder 收齐 3 条,顺序 [0,1,2].

        验证:created 后即可收,on_message 派发,业务自带 id reorder.
        """
        await self.client.create('1', 'sender')
        await self.client.start('1', 'sender')
        stopped = await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '1',
            timeout=10,
        )
        # Sender result = Receiver result = {'received': 3}
        self.assertEqual(stopped.get('result'), {'received': 3})
        # 即使 id=0 延迟到达,reorder 后顺序仍是 [0, 1, 2]
        self.assertEqual(
            [int(d['id']) for d in Receiver.received], [0, 1, 2],
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
