"""get_running_routines 单元测试.

dial-out(GrpcServerTransport):routine 经 Stream 发 routine.get_running ->
fake kernel 回 routine.get_running_reply,验证 future resolve.两种模式都问 kernel,
不读本地 runtime.
"""
import asyncio
import unittest

import grpc
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct

from routine import Routine, Routines, RoutineHub, GrpcServerTransport
from routine.grpc import routine_pb2_grpc
from routine.protocol import ROUTINE_GET_RUNNING, ROUTINE_GET_RUNNING_REPLY

LIFECYCLE_CREATED = 'lifecycle.created'
LIFECYCLE_START = 'lifecycle.start'


class QueryRunner(Routine):
    """run 里调 get_running_routines,把结果作为 stopped result 返回."""

    async def run(self, kwargs):
        return await self.get_running_routines()


class _FakeKernel:
    """gRPC bidi Stream client,模拟 kernel:收到 routine.get_running 回 reply.

    其它事件(lifecycle.started/stopped)入队供断言.
    """

    def __init__(self, addr, reply_routines):
        self.channel = grpc.aio.insecure_channel(addr)
        self.stub = routine_pb2_grpc.RoutineServiceStub(self.channel)
        self._call = self.stub.Stream()
        self._reply_routines = reply_routines
        self.events: asyncio.Queue = asyncio.Queue()
        self._reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for msg in self._call:
                d = MessageToDict(msg)
                ev = d.get('event', '')
                if ev == ROUTINE_GET_RUNNING:
                    req_id = d.get('req_id', '')
                    reply = {
                        'event': ROUTINE_GET_RUNNING_REPLY,
                        'req_id': req_id,
                        'routines': self._reply_routines,
                    }
                    s = Struct()
                    s.update(reply)
                    await self._call.write(s)
                else:
                    await self.events.put(d)
        except Exception:
            pass

    async def _send(self, d):
        s = Struct()
        s.update(d)
        await self._call.write(s)

    async def create(self, id, name):
        await self._send({'event': LIFECYCLE_CREATED, 'id': id, 'name': name})

    async def start(self, id, name):
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


class TestGetRunningRoutines(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rs = Routines()
        self.rs.register(QueryRunner)
        self.transport = GrpcServerTransport('127.0.0.1:0')
        self.server = RoutineHub(self.rs, transport=self.transport, hub_id='t')
        self.transport.attach(self.server)
        await self.transport.start()
        # fake kernel 回两条 running:bridge#5 + other#9.
        self.client = _FakeKernel(
            f'127.0.0.1:{self.transport.bound_port}',
            reply_routines=[{'name': 'agent_ws_bridge', 'id': '5'},
                            {'name': 'other', 'id': '9'}],
        )

    async def asyncTearDown(self):
        try:
            await self.client.close()
        except Exception:
            pass
        await self.transport.stop()

    async def test_dialout_get_running(self):
        """dial-out:QueryRunner.get_running_routines 经 Stream 往返拿回 kernel 的列表."""
        await self.client.create('1', 'query_runner')
        await self.client.start('1', 'query_runner')
        stopped = await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '1',
        )
        result = stopped.get('result')
        self.assertIsInstance(result, list)
        names = {r.get('name') for r in result}
        self.assertIn('agent_ws_bridge', names)
        self.assertIn('other', names)
        # id 透传(kernel 侧已是 string)
        bridge = next(r for r in result if r.get('name') == 'agent_ws_bridge')
        self.assertEqual(bridge.get('id'), '5')

    async def test_no_kernel_returns_empty(self):
        """没 kernel peer 连上时返 [](不阻塞等超时,agent 轮询重试)."""
        # 起一个临时 transport(无 client 连),直接调 transport.get_running_routines.
        t = GrpcServerTransport('127.0.0.1:0')
        rs = Routines()
        srv = RoutineHub(rs, transport=t, hub_id='t')
        t.attach(srv)
        await t.start()
        try:
            self.assertEqual(await t.get_running_routines(), [])
        finally:
            await t.stop()


if __name__ == '__main__':
    unittest.main(verbosity=2)
