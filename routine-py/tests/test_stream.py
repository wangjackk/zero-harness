"""streamreq(流式 request)单元测试.

in-process RoutineHub + kernel relay client(p2p.send → p2p.delivered).
覆盖:正常多帧 + eof / 消费方中途 cancel.
"""
import asyncio
import unittest

import grpc
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct

from routine import Routine, Routines, RoutineHub, stream, GrpcServerTransport
from routine.errors import StreamCancelled
from routine.grpc import routine_pb2_grpc
from routine.protocol import (
    MESSAGE_DELIVERED, MESSAGE_REQ, MESSAGE_REQ_DELIVERED, MESSAGE_REQ_REPLY,
    MESSAGE_REQ_REPLY_DELIVERED, MESSAGE_SEND, MESSAGE_STREAM_CANCEL,
    MESSAGE_STREAM_CANCEL_DELIVERED, MESSAGE_STREAM_DATA,
    MESSAGE_STREAM_DATA_DELIVERED, MESSAGE_STREAM_OPEN,
    MESSAGE_STREAM_OPEN_DELIVERED,
)

LIFECYCLE_CREATED = 'lifecycle.created'
LIFECYCLE_START = 'lifecycle.start'


class _Provider(Routine):
    """provider 基类:start 阻塞保持 running,stop 时 set evt."""

    def __init__(self):
        super().__init__()
        self._evt = asyncio.Event()

    async def run(self, kwargs):
        await self._evt.wait()

    async def stop(self):
        self._evt.set()


class Counter(_Provider):
    """@stream('count'):yield n 个数字.记录是否被 cancel(except CancelledError)."""

    cancelled: bool = False

    @stream('count')
    async def _on_count(self, source, data):
        n = int(data.get('n', 3))  # wire 上的 int 经 Struct 变 float64,转回
        try:
            for i in range(n):
                yield i
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            Counter.cancelled = True
            raise


class Collector(Routine):
    """stream_req Counter,收集所有 chunk 存 result.kwargs: take(收 N 帧后 break)."""

    async def run(self, kwargs):
        take = int(kwargs.get('take', 0))
        target = kwargs.get('target', '2')
        chunks = []
        async with await self.stream_req(target, 'count', {'n': 5}) as s:
            async for chunk in s:
                chunks.append(chunk)
                if take and len(chunks) >= take:
                    break  # 触发 __aexit__ cancel
        return {'chunks': chunks}


class _KernelRelayClient:
    """gRPC bidi Stream client,模拟 kernel broker.message.* send → 对应 delivered."""

    _MSG_DELIVERED = {
        MESSAGE_SEND: MESSAGE_DELIVERED,
        MESSAGE_REQ: MESSAGE_REQ_DELIVERED,
        MESSAGE_REQ_REPLY: MESSAGE_REQ_REPLY_DELIVERED,
        MESSAGE_STREAM_OPEN: MESSAGE_STREAM_OPEN_DELIVERED,
        MESSAGE_STREAM_DATA: MESSAGE_STREAM_DATA_DELIVERED,
        MESSAGE_STREAM_CANCEL: MESSAGE_STREAM_CANCEL_DELIVERED,
    }

    def __init__(self, addr):
        self.channel = grpc.aio.insecure_channel(addr)
        self.stub = routine_pb2_grpc.RoutineServiceStub(self.channel)
        self._call = self.stub.Stream()
        self.events: asyncio.Queue = asyncio.Queue()
        self._reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for msg in self._call:
                d = MessageToDict(msg)
                ev = d.get('event', '')
                if ev in self._MSG_DELIVERED:
                    await self._relay(d, self._MSG_DELIVERED[ev])
                else:
                    await self.events.put(d)
        except Exception:
            pass

    async def _relay(self, msg, delivered_event):
        target_ids = msg.get('target_ids', [])
        data = msg.get('data') or {}
        source_id = msg.get('source_id', '')
        for tid in target_ids:
            delivered = {
                'event': delivered_event,
                'target_id': tid,
                'data': data,
                'source': {'id': source_id},
            }
            s = Struct()
            s.update(delivered)
            await self._call.write(s)

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
        d = {'event': LIFECYCLE_START, 'id': id, 'name': name}
        await self._send(d)

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


class TestStream(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rs = Routines()
        self.rs.register(Counter, Collector)
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

    async def test_stream_full(self):
        """stream_req 收完 5 帧 + 正常 eof → chunks=[0,1,2,3,4]."""
        await self.client.create('2', 'counter')
        await self.client.start('2', 'counter')
        await self.client.recv(lambda m: m.get('event') == 'lifecycle.started' and m.get('id') == '2')
        await self.client.create('1', 'collector')
        await self.client.start('1', 'collector')
        stopped = await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '1',
            timeout=10,
        )
        # wire 上 int→float64,断言前转回 int
        result = stopped.get('result') or {}
        chunks = [int(c) for c in result.get('chunks', [])]
        self.assertEqual(chunks, [0, 1, 2, 3, 4])

    async def test_stream_cancel(self):
        """消费方收 2 帧后 break(exit async with)→ provider gen 被 cancel."""
        Counter.cancelled = False
        await self.client.create('2', 'counter')
        await self.client.start('2', 'counter')
        await self.client.recv(lambda m: m.get('event') == 'lifecycle.started' and m.get('id') == '2')
        await self.client.create('1', 'collector', {'take': 2})
        await self.client.start('1', 'collector')
        stopped = await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '1',
            timeout=10,
        )
        # Collector 只收了 2 帧(break 后 __aexit__ 发 cancel)
        result = stopped.get('result') or {}
        chunks = [int(c) for c in result.get('chunks', [])]
        self.assertEqual(chunks, [0, 1])
        # provider gen 被取消(cancel 帧到达后 task.cancel())
        # 给点时间 cancel 传播
        await asyncio.sleep(0.3)
        self.assertTrue(Counter.cancelled, 'provider gen 未被 cancel')


if __name__ == '__main__':
    unittest.main(verbosity=2)
