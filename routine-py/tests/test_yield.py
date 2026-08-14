"""yield(child→parent routine yield)单元测试.

in-process RoutineHub + kernel relay client(routine.yield → routine.yielded).
覆盖:正常多帧 + 终结 / child gen 抛异常 → parent 迭代 raise.
"""
import asyncio
import unittest

import grpc
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct

from routine import Routine, Routines, RoutineHub, GrpcServerTransport
from routine.grpc import routine_pb2_grpc
from routine.protocol import ROUTINE_YIELD, ROUTINE_YIELDED

LIFECYCLE_CREATED = 'lifecycle.created'
LIFECYCLE_START = 'lifecycle.start'


class Generator(Routine):
    """async-gen start:yield 3 项后结束."""

    async def run(self, kwargs):
        for i in range(3):
            yield {'i': i}


class BoomGen(Routine):
    """async-gen start:yield 1 项后抛异常."""

    async def run(self, kwargs):
        yield {'i': 0}
        raise RuntimeError('gen boom')


class Collector(Routine):
    """submit Generator,async for 收集所有 yield 项存 result."""

    async def run(self, kwargs):
        handle = await self.submit('generator', {})
        await handle.start()
        await handle.wait_started()
        items = []
        async for item in handle:
            items.append(item)
        return {'items': items}


class CollectorCatcher(Routine):
    """async for 一个会抛异常的 gen,捕获 RuntimeError 存 error."""

    error: str = ''

    async def run(self, kwargs):
        handle = await self.submit('boom_gen', {})
        await handle.start()
        await handle.wait_started()
        try:
            async for _ in handle:
                pass
        except RuntimeError as exc:
            CollectorCatcher.error = str(exc)
        return {'ok': True}


class _KernelRelayClient:
    """gRPC bidi Stream client,模拟 kernel 全套 broker 行为:
    - routine.submit → 分配 child_id,回 routine.submitted
    - routine.start → 发 lifecycle.start 给 server(Create+Start)
    - routine.stop → 发 lifecycle.stop
    - lifecycle.started/stopped → 回声给 server(kernel 中转,唤醒父 handle)
    - routine.yield → routine.yielded(dumb forward)
    """

    def __init__(self, addr):
        self.channel = grpc.aio.insecure_channel(addr)
        self.stub = routine_pb2_grpc.RoutineServiceStub(self.channel)
        self._call = self.stub.Stream()
        self.events: asyncio.Queue = asyncio.Queue()
        self._next_id = 10
        self._submit_meta: dict = {}
        self._pending_submits: dict = {}  # child_id → (name, kwargs)
        self._reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for msg in self._call:
                d = MessageToDict(msg)
                ev = d.get('event')
                if ev == 'routine.submit':
                    self._next_id += 1
                    cid = str(self._next_id)
                    self._submit_meta[cid] = (d.get('name', ''), d.get('kwargs') or {})
                    # 发 lifecycle.created(server handle_created 实例化+注册+建 inbox).
                    # 等 server 的 created 回报后才发 submitted(对标真实 kernel:submitted 时一定 created).
                    name, kwargs = self._submit_meta[cid]
                    created = {'event': 'lifecycle.created', 'id': cid, 'name': name}
                    if kwargs:
                        created['kwargs'] = kwargs
                    await self._write(created)
                    self._pending_submits[cid] = d.get('req_id', '')
                elif ev == 'lifecycle.created':
                    # server→kernel created 回报(无 name):发 submitted 给父
                    cid = d.get('id', '')
                    req_id = self._pending_submits.pop(cid, None)
                    if req_id:
                        await self._write({'event': 'routine.submitted',
                                           'req_id': req_id, 'child_id': cid})
                elif ev == 'routine.start':
                    # start 入参从 routine.start 事件透传(跟 submit kwargs 各自独立).
                    cid = d.get('child_id', '')
                    name, _ = self._submit_meta.get(cid, ('', {}))
                    p = {'event': 'lifecycle.start', 'id': cid, 'name': name}
                    start_kwargs = d.get('kwargs')
                    if start_kwargs:
                        p['kwargs'] = start_kwargs
                    await self._write(p)
                elif ev == 'routine.stop':
                    await self._write({'event': 'lifecycle.stop', 'id': d.get('child_id', '')})
                elif ev in ('lifecycle.started', 'lifecycle.stopped'):
                    # kernel 中转回声:转发给 py(唤醒 wait_started/wait)
                    await self._write(d)
                    await self.events.put(d)
                elif ev == ROUTINE_YIELD:
                    delivered = {'event': ROUTINE_YIELDED,
                                 'id': d.get('id', ''), 'is_final': d.get('is_final', False)}
                    if 'data' in d:
                        delivered['data'] = d['data']
                    if d.get('error'):
                        delivered['error'] = d['error']
                    await self._write(delivered)
                else:
                    await self.events.put(d)
        except Exception:
            pass

    async def _write(self, d):
        s = Struct()
        s.update(d)
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


class TestYield(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rs = Routines()
        self.rs.register(Generator, BoomGen, Collector, CollectorCatcher)
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

    async def test_yield_full(self):
        """Generator yield 3 项 → Collector submit+async for 收到 3 项 + 终结."""
        await self.client.create('1', 'collector')
        await self.client.start('1', 'collector')
        stopped = await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '1',
            timeout=10,
        )
        result = stopped.get('result') or {}
        items = result.get('items', [])
        # 每个 yield 是 {'i': N},i 经 wire 变 float
        self.assertEqual([int(it.get('i')) for it in items], [0, 1, 2])

    async def test_yield_error(self):
        """BoomGen yield 1 项后抛异常 → CollectorCatcher async for raise RuntimeError."""
        CollectorCatcher.error = ''
        await self.client.create('1', 'collector_catcher')
        await self.client.start('1', 'collector_catcher')
        await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '1',
            timeout=10,
        )
        self.assertTrue(CollectorCatcher.error, 'CollectorCatcher 未捕获 RuntimeError')
        self.assertIn('gen boom', CollectorCatcher.error)


if __name__ == '__main__':
    unittest.main(verbosity=2)
