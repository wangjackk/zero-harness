"""req(request/reply)单元测试.

in-process RoutineHub + 一个 gRPC client 模拟 kernel broker:收 p2p.send
→ 回送 p2p.delivered(正是 kernel 做的 dumb forward).覆盖:正常 / 超时 /
handler 抛异常.
"""
import asyncio
import unittest

import grpc
from routine.protocol import dict_to_frame, frame_to_dict

from routine import Routine, Routines, RoutineHub, request, GrpcServerTransport
from routine.errors import ReqError, ReqTimeout
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
    """provider 基类:start 阻塞在 evt 上保持 running(才能收 p2p),stop 时 set."""

    def __init__(self):
        super().__init__()
        self._evt = asyncio.Event()

    async def run(self, kwargs):
        await self._evt.wait()

    async def stop(self):
        self._evt.set()


class Builder(_Provider):
    @request('build')
    async def _on_build(self, source, data):
        return {'built': data.get('x', 0) * 2}


class BoomBuilder(_Provider):
    @request('build')
    async def _on_build(self, source, data):
        raise RuntimeError('build failed')


class Asker(Routine):
    """start 里 req builder,把 result 存到 result 字段(= stopped result).

    kwargs: target(对端 rid,默认 '2')/ timeout(默认 30s).
    """

    async def run(self, kwargs):
        target = kwargs.get('target', '2')
        timeout = kwargs.get('timeout', 30.0)
        return await self.req(target, 'build', {'x': 21}, timeout=timeout)


class AskerCatcher(Routine):
    """req 一个会抛异常的 handler,捕获 ReqError 存到 error 字段."""

    error: str = ''

    async def run(self, kwargs):
        target = kwargs.get('target', '2')
        timeout = kwargs.get('timeout', 30.0)
        try:
            await self.req(target, 'build', {'x': 1}, timeout=timeout)
        except ReqError as exc:
            AskerCatcher.error = str(exc)
        return {'ok': True}


class _KernelRelayClient:
    """gRPC bidi Stream client,模拟 kernel broker.

    message.* send 类事件 → 对应 delivered 类事件(dumb forward by target_id).
    旧 p2p.send → p2p.delivered 也兼容(保留过渡).
    其它事件(lifecycle.started/stopped)放进 events 队列供断言.
    """

    # message.* send → delivered 配对(对标 kernel broker.OnMessage).
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
                d = frame_to_dict(msg)
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
            s = dict_to_frame(delivered)
            await self._call.write(s)

    async def _send(self, d):
        s = dict_to_frame(d)
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
                raise asyncio.TimeoutError(f'recv timeout; last events drained')
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


class TestReq(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rs = Routines()
        self.rs.register(Builder, BoomBuilder, Asker, AskerCatcher)
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

    async def test_req_ok(self):
        """Asker req Builder('build', {x:21}) → result {'built': 42}."""
        await self.client.create('2', 'builder')          # 先起 provider
        await self.client.start('2', 'builder')
        await self.client.recv(lambda m: m.get('event') == 'lifecycle.started' and m.get('id') == '2')
        await self.client.create('1', 'asker')            # Asker.target_id='2'
        await self.client.start('1', 'asker')
        stopped = await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '1',
        )
        self.assertEqual(stopped.get('result'), {'built': 42})

    async def test_req_handler_error(self):
        """Builder handler 抛异常 → Asker 收 ReqError."""
        await self.client.create('2', 'boom_builder')
        await self.client.start('2', 'boom_builder')
        await self.client.recv(lambda m: m.get('event') == 'lifecycle.started' and m.get('id') == '2')
        AskerCatcher.error = ''
        await self.client.create('1', 'asker_catcher')
        await self.client.start('1', 'asker_catcher')
        await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '1',
        )
        self.assertTrue(AskerCatcher.error, 'AskerCatcher 未捕获 ReqError')
        self.assertIn('build failed', AskerCatcher.error)

    async def test_req_timeout(self):
        """req 到不存在的 target → 超时 ReqTimeout(不挂死)."""
        await self.client.create('1', 'asker', {'target': '999', 'timeout': 1.0})
        await self.client.start('1', 'asker')
        # Asker.start req '999' 超时 → 抛 ReqTimeout → start 异常 → stopped ERROR
        stopped = await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '1',
            timeout=10,
        )
        self.assertEqual(stopped.get('reason'), 'ERROR')


if __name__ == '__main__':
    unittest.main(verbosity=2)
