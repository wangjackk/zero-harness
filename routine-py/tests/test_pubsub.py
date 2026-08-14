"""pubsub 单元测试.

in-process RoutineHub + kernel relay client:subscribe/publish 经 kernel
订阅表 fanout,unsubscribe 退订,routine stop 自动退订.
"""
import asyncio
import unittest

import grpc
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct

from routine import Routine, Routines, RoutineHub, subscribe, GrpcServerTransport
from routine.grpc import routine_pb2_grpc
from routine.protocol import (
    PUBSUB_DELIVERED, PUBSUB_PUBLISH, PUBSUB_SUBSCRIBE, PUBSUB_UNSUBSCRIBE,
)

LIFECYCLE_CREATED = 'lifecycle.created'
LIFECYCLE_START = 'lifecycle.start'
LIFECYCLE_STOP = 'lifecycle.stop'


class _Provider(Routine):
    """长跑 routine:start 阻塞保持 running 才能收 pubsub.delivered."""

    def __init__(self):
        super().__init__()
        self._evt = asyncio.Event()

    async def run(self, kwargs):
        await self._evt.wait()

    async def stop(self):
        self._evt.set()


class Listener(_Provider):
    """@subscribe('tick'):收到的消息存 class-level received 列表."""

    received: list = []

    @subscribe('tick')
    async def _on_tick(self, source, data):
        Listener.received.append(data)


class Ticker(Routine):
    """publish 'tick' 三次,每次带不同 data."""

    target: str = '0'  # publish 不需要 target

    async def run(self, kwargs):
        for i in range(3):
            await self.publish('tick', {'i': i})
            await asyncio.sleep(0.02)
        return {'published': 3}


class _KernelRelayClient:
    """gRPC bidi Stream client,模拟 kernel:subscribe/publish/unpublish → 维护
    订阅表 + fanout delivered;lifecycle.stop 触发自动退订."""

    def __init__(self, addr):
        self.channel = grpc.aio.insecure_channel(addr)
        self.stub = routine_pb2_grpc.RoutineServiceStub(self.channel)
        self._call = self.stub.Stream()
        self.events: asyncio.Queue = asyncio.Queue()
        self._subs: dict = {}  # (namespace, topic) → set of subscriber id
        self._reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for msg in self._call:
                d = MessageToDict(msg)
                ev = d.get('event')
                if ev == PUBSUB_SUBSCRIBE:
                    ns = d.get('namespace', '') or ''
                    key = (ns, d.get('topic', ''))
                    self._subs.setdefault(key, set()).add(d.get('id', ''))
                elif ev == PUBSUB_UNSUBSCRIBE:
                    ns = d.get('namespace', '') or ''
                    key = (ns, d.get('topic', ''))
                    s = self._subs.get(key)
                    if s:
                        s.discard(d.get('id', ''))
                elif ev == PUBSUB_PUBLISH:
                    await self._fanout(d)
                else:
                    await self.events.put(d)
        except Exception:
            pass

    async def _fanout(self, msg):
        topic = msg.get('topic', '')
        ns = msg.get('namespace', '') or ''
        data = msg.get('data')
        source_id = msg.get('source_id', '')
        for sid in list(self._subs.get((ns, topic), ())):
            delivered = {
                'event': PUBSUB_DELIVERED,
                'subscriber_id': sid,
                'topic': topic,
                'namespace': ns,
                'source': {'id': source_id},
            }
            if data is not None:
                delivered['data'] = data
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

    async def stop(self, id):
        await self._send({'event': LIFECYCLE_STOP, 'id': id})
        # 模拟 kernel:lifecycle.stopped 时清掉该 id 所有订阅
        for s in self._subs.values():
            s.discard(id)

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


class TestPubsub(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rs = Routines()
        self.rs.register(Listener, Ticker)
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

    async def test_fanout(self):
        """2 个 listener 订阅 'tick',ticker 发 3 条 → 两个各收 3 条."""
        Listener.received = []
        await self.client.create('1', 'listener')
        await self.client.start('1', 'listener')
        await self.client.create('2', 'listener')
        await self.client.start('2', 'listener')
        await self.client.recv(lambda m: m.get('event') == 'lifecycle.started' and m.get('id') == '1')
        await self.client.recv(lambda m: m.get('event') == 'lifecycle.started' and m.get('id') == '2')
        await self.client.create('3', 'ticker')
        await self.client.start('3', 'ticker')
        await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '3',
            timeout=10,
        )
        # 两个 listener 各收 3 条
        self.assertEqual(len(Listener.received), 6)
        # 收到的 i 值是 0,1,2(float64),转 int 校验
        got = sorted(int(d.get('i')) for d in Listener.received)
        self.assertEqual(got, [0, 0, 1, 1, 2, 2])

    async def test_auto_unsubscribe_on_stop(self):
        """listener stop 后再 publish → 该 listener 不再收到(kernel 自动退订)."""
        Listener.received = []
        await self.client.create('1', 'listener')
        await self.client.start('1', 'listener')
        await self.client.recv(lambda m: m.get('event') == 'lifecycle.started' and m.get('id') == '1')
        # 停掉 listener(kernel 自动退订)
        await self.client.stop('1')
        await self.client.recv(lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '1')
        # 再 publish(无订阅者,fanout 空跑)
        await self.client.create('3', 'ticker')
        await self.client.start('3', 'ticker')
        await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '3',
            timeout=10,
        )
        self.assertEqual(Listener.received, [], 'stopped listener 仍收到 pubsub(未自动退订)')


class NsListener(_Provider):
    """动态订阅两个 namespace 的 'tick',分别存到 ns_a / ns_b 列表."""

    ns_a: list = []
    ns_b: list = []

    async def run(self, kwargs):
        async def on_a(source, data):
            NsListener.ns_a.append(data)
        async def on_b(source, data):
            NsListener.ns_b.append(data)
        await self.subscribe('tick', on_a, namespace='a')
        await self.subscribe('tick', on_b, namespace='b')
        await self._evt.wait()

    async def stop(self):
        self._evt.set()


class NsTicker(Routine):
    """向 namespace 'a' 发 2 条 'tick',向 namespace 'b' 发 1 条."""

    async def run(self, kwargs):
        await self.publish('tick', {'from': 'a1'}, namespace='a')
        await self.publish('tick', {'from': 'a2'}, namespace='a')
        await self.publish('tick', {'from': 'b1'}, namespace='b')
        await asyncio.sleep(0.05)  # 等 fanout 到达
        return {'published': 3}


class NsDefaultListener(_Provider):
    """订阅默认 namespace ('') 的 'tick'.验证 namespace='a' 的 publish 不串到 ''."""

    received: list = []

    @subscribe('tick')  # 默认 namespace=''
    async def _on_tick(self, source, data):
        NsDefaultListener.received.append(data)


class TestPubsubNamespace(unittest.IsolatedAsyncioTestCase):
    """namespace 隔离:同 topic 不同 namespace 互不串扰."""

    async def asyncSetUp(self):
        self.rs = Routines()
        self.rs.register(NsListener, NsTicker, NsDefaultListener)
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

    async def test_namespace_isolation(self):
        """NsListener 订阅 ns='a' 和 ns='b' 的 'tick';NsDefaultListener 订阅 ns='' 的 'tick'.
        NsTicker 发 a×2 + b×1(不发 ns='')→ NsListener.ns_a=2, ns_b=1, NsDefaultListener=0."""
        NsListener.ns_a = []
        NsListener.ns_b = []
        NsDefaultListener.received = []
        await self.client.create('1', 'ns_listener')
        await self.client.start('1', 'ns_listener')
        await self.client.create('2', 'ns_default_listener')
        await self.client.start('2', 'ns_default_listener')
        await self.client.recv(lambda m: m.get('event') == 'lifecycle.started' and m.get('id') == '1')
        await self.client.recv(lambda m: m.get('event') == 'lifecycle.started' and m.get('id') == '2')
        await self.client.create('3', 'ns_ticker')
        await self.client.start('3', 'ns_ticker')
        await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '3',
            timeout=10,
        )
        # ns='a' 收 2 条,ns='b' 收 1 条
        self.assertEqual(len(NsListener.ns_a), 2, f'ns_a 应收 2 条,实际 {NsListener.ns_a}')
        self.assertEqual(len(NsListener.ns_b), 1, f'ns_b 应收 1 条,实际 {NsListener.ns_b}')
        # 默认 namespace 收 0 条(ns='a' 的 publish 不串到 ns='')
        self.assertEqual(NsDefaultListener.received, [],
                         f'默认 ns 不应收到,实际 {NsDefaultListener.received}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
