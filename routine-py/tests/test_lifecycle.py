"""routine SDK 生命周期单元测试.

in-process:起真实 RoutineHub 在 127.0.0.1 随机端口,用最小 python gRPC client
发 lifecycle 事件验证 server 行为.覆盖:正常 done / start-stop / error /
not-found / stop 超时 / peer 断连 force_stop / Req 查询.
"""
import asyncio
import unittest

import grpc
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct

from routine import Routine, Routines, RoutineHandle, RoutineHub, GrpcServerTransport
from routine.grpc import routine_pb2_grpc

LIFECYCLE_CREATED = 'lifecycle.created'
LIFECYCLE_START = 'lifecycle.start'
LIFECYCLE_STOP = 'lifecycle.stop'
LIFECYCLE_STARTED = 'lifecycle.started'
LIFECYCLE_STOPPED = 'lifecycle.stopped'


class NormalRoutine(Routine):
    meta = {'description': 'a normal routine', 'tags': ['test']}

    async def run(self, kwargs):
        return 'done'


class HiddenRoutine(Routine):
    """hidden routine:meta['hidden']=True,get_routines 仍带 meta(含 hidden)."""
    meta = {'hidden': True}

    async def run(self, kwargs):
        return 'hidden'


class LongRoutine(Routine):
    """长跑 routine:start 阻塞在 event 上,stop 时 set."""

    def __init__(self):
        super().__init__()
        self._evt = asyncio.Event()

    async def run(self, kwargs):
        await self._evt.wait()

    async def stop(self):
        self._evt.set()


class ErrorRoutine(Routine):
    async def run(self, kwargs):
        raise RuntimeError('boom')


class TimeoutRoutine(Routine):
    """stop 阻塞,触发 STOP_TIMEOUT 超时 cancel 路径."""

    def __init__(self):
        super().__init__()
        self._evt = asyncio.Event()

    async def run(self, kwargs):
        await self._evt.wait()

    async def stop(self):
        await asyncio.sleep(10)


class NoStopRoutine(Routine):
    """start 卡在 sleep,stop() no-op 立即返回.

    验证 stop() 不配合时 main task 仍被 cancel(不泄漏).
    """

    cancelled = False  # class-level:start 被 cancel 时 set

    async def run(self, kwargs):
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            NoStopRoutine.cancelled = True
            raise

    async def stop(self):
        pass  # no-op:不 set 任何 event,不 cancel


class StopRaisesRoutine(Routine):
    """start 长跑;stop() 抛异常.验证 stop() 异常不阻塞 cleanup + main task 仍终止."""

    cancelled = False

    def __init__(self):
        super().__init__()
        self._evt = asyncio.Event()

    async def run(self, kwargs):
        try:
            await self._evt.wait()
        except asyncio.CancelledError:
            StopRaisesRoutine.cancelled = True
            raise

    async def stop(self):
        raise RuntimeError('stop boom')


class StopBlocksRoutine(Routine):
    """start 长跑;stop() 永久阻塞(超时).验证超时后 fallback cancel main task."""

    cancelled = False

    def __init__(self):
        super().__init__()
        self._evt = asyncio.Event()

    async def run(self, kwargs):
        try:
            await self._evt.wait()
        except asyncio.CancelledError:
            StopBlocksRoutine.cancelled = True
            raise

    async def stop(self):
        await asyncio.sleep(100)  # 永不返回


class _TestClient:
    """最小 gRPC client:开一条 Stream,发 lifecycle 事件,收回报."""

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
                if d.get('event') == LIFECYCLE_CREATED:
                    # created 回报:测试不关心,丢弃(避免污染无 predicate 的 recv FIFO)
                    continue
                self.events.put_nowait(d)
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
        d = {'event': LIFECYCLE_START, 'id': id, 'name': name}
        await self._send(d)

    async def stop(self, id):
        await self._send({'event': LIFECYCLE_STOP, 'id': id})

    async def req(self, msg):
        s = Struct()
        s.update(msg)
        resp = await self.stub.Req(s)
        return MessageToDict(resp)

    async def recv(self, timeout=2.0):
        return await asyncio.wait_for(self.events.get(), timeout=timeout)

    async def close(self):
        self._reader.cancel()
        try:
            await self._call.done_writing()
        except Exception:
            pass
        await self.channel.close()


class TestLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rs = Routines()
        self.rs.register(NormalRoutine, HiddenRoutine, LongRoutine, ErrorRoutine,
                            TimeoutRoutine, NoStopRoutine, StopRaisesRoutine,
                            StopBlocksRoutine)
        self.transport = GrpcServerTransport('127.0.0.1:0')
        self.server = RoutineHub(self.rs, modules=['body'], transport=self.transport, hub_id='t')
        self.server.lifecycle.STOP_TIMEOUT = 0.5  # 加速超时用例
        self.transport.attach(self.server)
        await self.transport.start()
        self.client = _TestClient(f'127.0.0.1:{self.transport.bound_port}')

    async def asyncTearDown(self):
        try:
            await self.client.close()
        except Exception:
            pass
        await self.transport.stop()

    async def test_start_done(self):
        """start 正常返回 → started + stopped(AUTO, auto)."""
        await self.client.create('1', 'normal_routine')
        await self.client.start('1', 'normal_routine')
        started = await self.client.recv()
        self.assertEqual(started['event'], 'lifecycle.started')
        stopped = await self.client.recv()
        self.assertEqual(stopped['event'], 'lifecycle.stopped')
        self.assertEqual(stopped['reason'], 'AUTO')

    async def test_start_stop(self):
        """start 长跑 → started → stop → stopped(STOP)."""
        await self.client.create('1', 'long_routine')
        await self.client.start('1', 'long_routine')
        self.assertEqual((await self.client.recv())['event'], 'lifecycle.started')
        await self.client.stop('1')
        stopped = await self.client.recv()
        self.assertEqual(stopped['event'], 'lifecycle.stopped')
        self.assertEqual(stopped['reason'], 'STOP')

    async def test_start_error(self):
        """start 抛异常 → started + stopped(ERROR) + error 字段带原异常文本."""
        await self.client.create('1', 'error_routine')
        await self.client.start('1', 'error_routine')
        self.assertEqual((await self.client.recv())['event'], 'lifecycle.started')
        stopped = await self.client.recv()
        self.assertEqual(stopped['event'], 'lifecycle.stopped')
        self.assertEqual(stopped['reason'], 'ERROR')
        # error 字段透传原异常文本----父 handle.wait() 能拿到具体原因,不只 reason.
        self.assertEqual(stopped.get('error'), 'boom')

    async def test_routine_not_found(self):
        """未知 routine → stopped(ERROR)(不发 started)."""
        await self.client.create('1', 'nope')
        stopped = await self.client.recv()
        self.assertEqual(stopped['event'], 'lifecycle.stopped')
        self.assertEqual(stopped['reason'], 'ERROR')

    async def test_stop_timeout(self):
        """stop 阻塞超时 → cancel fallback → stopped(STOP) + instance 清理."""
        await self.client.create('1', 'timeout_routine')
        await self.client.start('1', 'timeout_routine')
        self.assertEqual((await self.client.recv())['event'], 'lifecycle.started')
        await self.client.stop('1')
        stopped = await self.client.recv()
        self.assertEqual(stopped['reason'], 'STOP')
        await asyncio.sleep(0.2)
        self.assertEqual(len(self.server.runtime.running_instances), 0)

    async def test_stop_noop_cancels_main(self):
        """stop() no-op 立即返回 → main task 必须被 cancel(不泄漏)."""
        NoStopRoutine.cancelled = False
        await self.client.create('1', 'no_stop_routine')
        await self.client.start('1', 'no_stop_routine')
        self.assertEqual((await self.client.recv())['event'], 'lifecycle.started')
        await self.client.stop('1')
        stopped = await self.client.recv()
        self.assertEqual(stopped['reason'], 'STOP')
        await asyncio.sleep(1.0)
        self.assertTrue(NoStopRoutine.cancelled, 'main task 未被 cancel(泄漏)')
        self.assertEqual(len(self.server.runtime.running_instances), 0)

    async def test_stop_raises_still_cancels(self):
        """stop() 抛异常 → 不崩溃,main task 仍被 cancel,stopped(STOP) + 清理."""
        StopRaisesRoutine.cancelled = False
        await self.client.create('1', 'stop_raises_routine')
        await self.client.start('1', 'stop_raises_routine')
        self.assertEqual((await self.client.recv())['event'], 'lifecycle.started')
        await self.client.stop('1')
        stopped = await self.client.recv()
        self.assertEqual(stopped['reason'], 'STOP')
        await asyncio.sleep(1.0)
        self.assertTrue(StopRaisesRoutine.cancelled, 'stop() 异常后 main task 未被 cancel')
        self.assertEqual(len(self.server.runtime.running_instances), 0)

    async def test_stop_blocks_timeout_cancels(self):
        """stop() 永久阻塞 → stop() 超时后 main task 仍被 cancel(双重超时兜底)."""
        StopBlocksRoutine.cancelled = False
        await self.client.create('1', 'stop_blocks_routine')
        await self.client.start('1', 'stop_blocks_routine')
        self.assertEqual((await self.client.recv())['event'], 'lifecycle.started')
        await self.client.stop('1')
        stopped = await self.client.recv(timeout=5.0)
        self.assertEqual(stopped['reason'], 'STOP')
        await asyncio.sleep(1.0)
        self.assertTrue(StopBlocksRoutine.cancelled, 'stop() 超时后 main task 未被 cancel')
        self.assertEqual(len(self.server.runtime.running_instances), 0)

    async def test_force_stop_peer(self):
        """client 断连 → server force_stop_peer 清理 instance."""
        await self.client.create('1', 'long_routine')
        await self.client.start('1', 'long_routine')
        self.assertEqual((await self.client.recv())['event'], 'lifecycle.started')
        await self.client.close()
        # 等 server 检测断连 + force_stop_peer 收尾
        await asyncio.sleep(1.5)
        self.assertEqual(len(self.server.runtime.running_instances), 0)

    async def test_req_get_routines(self):
        """Req get_routines 返回所有注册 routine."""
        resp = await self.client.req({'event': 'get_routines'})
        names = sorted(r['name'] for r in resp['routines'])
        self.assertEqual(
            names,
            ['error_routine', 'hidden_routine', 'long_routine', 'no_stop_routine',
             'normal_routine', 'stop_blocks_routine', 'stop_raises_routine',
             'timeout_routine'],
        )

    async def test_req_get_routines_meta(self):
        """get_routines 带 meta 字段:类级 meta dict 序列化到 wire.

        NormalRoutine.meta = {'description', 'tags'};HiddenRoutine.meta = {'hidden': True}.
        无 meta 声明的 routine(如 LongRoutine)回空 dict(不缺字段).
        """
        resp = await self.client.req({'event': 'get_routines'})
        by_name = {r['name']: r for r in resp['routines']}

        # NormalRoutine:自由扩展字段原样透传
        self.assertEqual(by_name['normal_routine']['meta'], {
            'description': 'a normal routine', 'tags': ['test'],
        })
        # HiddenRoutine:hidden=True 带回
        self.assertEqual(by_name['hidden_routine']['meta'], {'hidden': True})
        # 未声明 meta 的 routine:空 dict(字段必在,不缺)
        self.assertEqual(by_name['long_routine']['meta'], {})

    async def test_on_inbound_relays_started_stopped_to_handle(self):
        """kernel 中转回来的 lifecycle.started/stopped 按 id 路由到父 handle.

        验证去掉本地 notify 抄近路后,handle 的 started/done 由 on_inbound 驱动.
        """
        handle = RoutineHandle('42', 'child')
        self.server.runtime.register_handle('42', handle)
        self.assertFalse(handle.is_started())

        # 模拟 kernel 中转回来的 lifecycle.started
        await self.server.on_inbound({'event': LIFECYCLE_STARTED, 'id': '42'})
        self.assertTrue(handle.is_started())
        self.assertFalse(handle.is_done())

        # 模拟 kernel 中转回来的 lifecycle.stopped(带 result)
        await self.server.on_inbound({
            'event': LIFECYCLE_STOPPED, 'id': '42', 'reason': 'STOP',
            'result': {'ok': True},
        })
        self.assertTrue(handle.is_done())
        self.assertEqual(handle.result, {'ok': True})
        self.assertIsNone(handle.error)
        # handle 已 pop,二次中转是 no-op
        self.assertIsNone(self.server.runtime.get_handle('42'))

    async def test_on_inbound_stopped_error_sets_handle_error(self):
        """kernel 中转 stopped reason=ERROR → handle.error 置位,wait() 会 raise."""
        handle = RoutineHandle('43', 'child')
        self.server.runtime.register_handle('43', handle)
        await self.server.on_inbound({
            'event': LIFECYCLE_STOPPED, 'id': '43', 'reason': 'ERROR',
        })
        self.assertTrue(handle.is_done())
        self.assertIsNotNone(handle.error)

    async def test_on_inbound_unknown_id_ignored(self):
        """kernel 中转回来的 id 没有对应 handle(Go 父直启)→ 静默丢弃,不报错."""
        await self.server.on_inbound({'event': LIFECYCLE_STARTED, 'id': '999'})
        await self.server.on_inbound({
            'event': LIFECYCLE_STOPPED, 'id': '999', 'reason': 'STOP',
        })
        # 没有异常即通过


if __name__ == '__main__':
    unittest.main(verbosity=2)
