"""get_module_tree 单元测试.

dial-out(GrpcServerTransport):routine 经 Stream 发 routine.get_module_tree ->
fake kernel 回 routine.get_module_tree_reply(带 tree payload),验证 future resolve +
runtime.module_tree 缓存刷新.两种模式都问 kernel,不读本地 runtime.

对照 test_get_running.py:get_running 回 routines 列表,get_module_tree 回 tree 拓扑并
刷新本地缓存(conflict 据此算).
"""
import asyncio
import unittest

import grpc
from routine.protocol import dict_to_frame, frame_to_dict

from routine import Routine, Routines, RoutineHub, GrpcServerTransport
from routine.grpc import routine_pb2_grpc
from routine.protocol import ROUTINE_GET_MODULE_TREE, ROUTINE_GET_MODULE_TREE_REPLY

LIFECYCLE_CREATED = 'lifecycle.created'
LIFECYCLE_START = 'lifecycle.start'

# 假 tree payload:对标 kernel module.Default().Serialize() 输出(flat map keyed by module_id).
# a/b 带 name(演示 name 可重复/可 != id),root 无 name(缺省=id).
FAKE_TREE = {'root': 'root', 'modules': {
    'root': {'children': ['a', 'b']},
    'a': {'name': 'A模块'},
    'b': {'name': 'B模块'},
}}


class QueryRunner(Routine):
    """run 里调 get_module_tree,把 root_id 作为 stopped result 返回(验往返 + 缓存刷新)."""

    async def run(self, kwargs):
        tree = await self.get_module_tree()
        return tree.root_id if tree is not None else None


class _FakeKernel:
    """gRPC bidi Stream client,模拟 kernel:收到 routine.get_module_tree 回 reply(带 tree).

    其它事件(lifecycle.started/stopped)入队供断言.
    """

    def __init__(self, addr, reply_tree):
        self.channel = grpc.aio.insecure_channel(addr)
        self.stub = routine_pb2_grpc.RoutineServiceStub(self.channel)
        self._call = self.stub.Stream()
        self._reply_tree = reply_tree
        self.events: asyncio.Queue = asyncio.Queue()
        self._reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for msg in self._call:
                d = frame_to_dict(msg)
                ev = d.get('event', '')
                if ev == ROUTINE_GET_MODULE_TREE:
                    req_id = d.get('req_id', '')
                    reply = {
                        'event': ROUTINE_GET_MODULE_TREE_REPLY,
                        'req_id': req_id,
                        'ok': True,
                        'tree': self._reply_tree,
                    }
                    s = dict_to_frame(reply)
                    await self._call.write(s)
                else:
                    await self.events.put(d)
        except Exception:
            pass

    async def _send(self, d):
        s = dict_to_frame(d)
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


class TestGetModuleTree(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rs = Routines()
        self.rs.register(QueryRunner)
        self.transport = GrpcServerTransport('127.0.0.1:0')
        self.server = RoutineHub(self.rs, transport=self.transport, hub_id='t')
        self.transport.attach(self.server)
        await self.transport.start()
        # 连上前 module_tree 缓存为 None(kernel 未推).
        self.assertIsNone(self.server.runtime.module_tree)
        self.client = _FakeKernel(
            f'127.0.0.1:{self.transport.bound_port}',
            reply_tree=FAKE_TREE,
        )

    async def asyncTearDown(self):
        try:
            await self.client.close()
        except Exception:
            pass
        await self.transport.stop()

    async def test_dialout_get_module_tree(self):
        """dial-out:QueryRunner.get_module_tree 经 Stream 往返拿回 tree + 刷缓存."""
        await self.client.create('1', 'query_runner')
        await self.client.start('1', 'query_runner')
        stopped = await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '1',
        )
        # run 返回 root_id(验往返拿到的 tree 解析正确).
        self.assertEqual(stopped.get('result'), 'root')
        # 缓存已刷新:runtime.module_tree 非 None 且 root_id 对.
        tree = self.server.runtime.module_tree
        self.assertIsNotNone(tree)
        self.assertEqual(tree.root_id, 'root')
        # name 透传:FAKE_TREE 里 a 带 name,root 缺省=id
        self.assertEqual(tree.name_of('a'), 'A模块')
        self.assertEqual(tree.name_of('root'), 'root')

    async def test_dialout_cache_enables_conflict(self):
        """刷后 conflict 可用(不再因树未缓存抛 RuntimeError)."""
        # 等 fake kernel 在 server 侧注册 out_queue(serve_stream 异步起,直接调会撞空队列竞速).
        deadline = asyncio.get_event_loop().time() + 2.0
        while not self.transport._out_queues:
            if asyncio.get_event_loop().time() > deadline:
                raise asyncio.TimeoutError('peer out_queue not registered')
            await asyncio.sleep(0.02)
        # 直接调 transport 拉一次(不经 lifecycle),刷缓存.
        tree = await self.transport.get_module_tree()
        self.assertIsNotNone(tree)
        self.assertEqual(tree.root_id, 'root')
        # 同模块 cone 相交 -> conflict True;不同子树 -> False.
        self.assertTrue(tree.conflict(['a'], ['a']))
        self.assertFalse(tree.conflict(['a'], ['b']))

    async def test_no_kernel_returns_none(self):
        """没 kernel peer 连上时返当前缓存(None)--不阻塞等超时."""
        t = GrpcServerTransport('127.0.0.1:0')
        rs = Routines()
        srv = RoutineHub(rs, transport=t, hub_id='t')
        t.attach(srv)
        await t.start()
        try:
            self.assertIsNone(await t.get_module_tree())
        finally:
            await t.stop()


if __name__ == '__main__':
    unittest.main(verbosity=2)
