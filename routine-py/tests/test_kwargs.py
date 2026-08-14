"""submit kwargs 是 start 的唯一入参来源 ---- 回归守卫.

直接驱动 server 的 lifecycle.created(投递 submit kwargs 给 created() + 存入
instance._init_kwargs)+ lifecycle.start(不带 kwargs,start() 用 created 时存的
那份),验证:
  1. submit kwargs 同时到 created() 和 start()(同一份,不是各自独立);
  2. submit 不带 kwargs 时 created() 与 start() 都收到 {};
  3. created() 和 start() 拿到的是同一个 dict.

对标真实 kernel:handleReverseSubmit 发 lifecycle.created 带 submit kwargs;
OnStartChild → Start → runRemote 发 lifecycle.start 不带 kwargs.start() 收到的
是 created 时存入 instance._init_kwargs 的那份 submit kwargs.
"""
import asyncio
import unittest

import grpc
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct

from routine import Routine, Routines, RoutineHub, GrpcServerTransport
from routine.grpc import routine_pb2_grpc

LIFECYCLE_CREATED = 'lifecycle.created'
LIFECYCLE_START = 'lifecycle.start'
LIFECYCLE_STARTED = 'lifecycle.started'
LIFECYCLE_STOPPED = 'lifecycle.stopped'


class Dual(Routine):
    """记录 created / start 各自收到的 kwargs(class-level,供测试断言)."""

    created_kwargs: dict = {}
    start_kwargs: dict = {}

    @classmethod
    def reset(cls):
        cls.created_kwargs = {}
        cls.start_kwargs = {}

    async def on_created(self, rid=None, kwargs=None):
        Dual.created_kwargs = kwargs or {}
        # 不占模块----返回 None(基类默认也是 None)

    async def run(self, kwargs):
        Dual.start_kwargs = kwargs or {}
        return {'created': Dual.created_kwargs, 'start': kwargs}


class _TestClient:
    """最小 gRPC client:开一条 Stream,发 lifecycle.created/start,收回报."""

    def __init__(self, addr):
        self.channel = grpc.aio.insecure_channel(addr)
        self.stub = routine_pb2_grpc.RoutineServiceStub(self.channel)
        self._call = self.stub.Stream()
        self.events: asyncio.Queue = asyncio.Queue()
        self._reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for msg in self._call:
                self.events.put_nowait(MessageToDict(msg))
        except Exception:
            pass

    async def _send(self, d):
        s = Struct()
        s.update(d)
        await self._call.write(s)

    async def create(self, id, name, kwargs=None):
        d = {'event': LIFECYCLE_CREATED, 'id': id, 'name': name}
        if kwargs:
            d['kwargs'] = kwargs
        await self._send(d)

    async def start(self, id, name):
        # start 不带 kwargs----start 用 created 时存入 _init_kwargs 的那份 submit kwargs.
        await self._send({'event': LIFECYCLE_START, 'id': id, 'name': name})

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


class TestKwargsSingleSource(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Dual.reset()
        self.rs = Routines()
        self.rs.register(Dual)
        self.transport = GrpcServerTransport('127.0.0.1:0')
        self.server = RoutineHub(self.rs, transport=self.transport, hub_id='t')
        self.transport.attach(self.server)
        await self.transport.start()
        self.client = _TestClient(f'127.0.0.1:{self.transport.bound_port}')

    async def asyncTearDown(self):
        try:
            await self.client.close()
        except Exception:
            pass
        await self.transport.stop()

    async def _drive(self, submit_kwargs):
        """created(submit kwargs)→ 等 created 回报 → start(不带 kwargs)→ 等 stopped."""
        await self.client.create('1', 'dual', submit_kwargs)
        # 等 server 的 created 回报(handle_created 发 lifecycle.created 回声)
        await self.client.recv(lambda m: m.get('event') == LIFECYCLE_CREATED
                               and m.get('id') == '1')
        await self.client.start('1', 'dual')
        await self.client.recv(lambda m: m.get('event') == LIFECYCLE_STARTED
                               and m.get('id') == '1')
        return await self.client.recv(lambda m: m.get('event') == LIFECYCLE_STOPPED
                                      and m.get('id') == '1')

    async def test_submit_kwargs_to_both_created_and_start(self):
        """submit kwargs 同时到 created() 和 start()(同一份)."""
        stopped = await self._drive({'submit': 'S'})
        self.assertEqual(Dual.created_kwargs, {'submit': 'S'})
        self.assertEqual(Dual.start_kwargs, {'submit': 'S'},
                         'start() 应收到 submit 的 kwargs(同一份)')
        result = stopped.get('result') or {}
        self.assertEqual(result.get('created'), {'submit': 'S'})
        self.assertEqual(result.get('start'), {'submit': 'S'})

    async def test_no_submit_kwargs(self):
        """submit 不带 kwargs → created() 与 start() 都收到 {}."""
        await self._drive(None)
        self.assertEqual(Dual.created_kwargs, {})
        self.assertEqual(Dual.start_kwargs, {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
