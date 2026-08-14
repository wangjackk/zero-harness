"""load_module / unload_module 测试.

两部分:
1. guard + ack 流程(stub IO,对标 test_acquire_release):
   - 未 started 调 load/unload 抛 RuntimeError,不发 wire.
   - started 后发 routine.load_module/unload_module 并等 ack.
   - ack ok=false -> 抛 LoadModuleError / UnloadModuleError.
2. dial-out 往返(gRPC fake kernel,对标 test_get_module_tree):
   - load 后 fake kernel 回 module_loaded + 重推 module.tree(含新 child).
   - routine 侧 get_module_tree() 主动拉刷新,conflict 覆盖新子模块.再 unload.
"""
import asyncio
import unittest

import grpc
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct

from routine import Routine, Routines, RoutineHub, GrpcServerTransport
from routine.ctx import RunContext
from routine.errors import LoadModuleError, UnloadModuleError
from routine.grpc import routine_pb2_grpc
from routine.protocol import (
    MODULE_TREE, ROUTINE_GET_MODULE_TREE, ROUTINE_GET_MODULE_TREE_REPLY,
    ROUTINE_LOAD_MODULE, ROUTINE_MODULE_LOADED,
    ROUTINE_UNLOAD_MODULE, ROUTINE_MODULE_UNLOADED,
)

LIFECYCLE_CREATED = 'lifecycle.created'
LIFECYCLE_START = 'lifecycle.start'


# ===== Part 1: guard + ack(stub IO)=====

class _StubRuntime:
    """持 load/unload ack future 表."""

    def __init__(self):
        self.load_futures = {}
        self.unload_futures = {}

    def register_load_future(self, req_id, fut):
        self.load_futures[req_id] = fut

    def pop_load_future(self, req_id):
        return self.load_futures.pop(req_id, None)

    def register_unload_future(self, req_id, fut):
        self.unload_futures[req_id] = fut

    def pop_unload_future(self, req_id):
        return self.unload_futures.pop(req_id, None)


class _StubIO:
    """记录发出的 routine.load_module/unload_module;持 stub runtime."""

    def __init__(self):
        self.sent = []
        self.runtime = _StubRuntime()

    async def send_routine_load_module(self, *, req_id, parent_id, child_id, name='', peer_id=None):
        self.sent.append(('load', req_id, parent_id, child_id, name))

    async def send_routine_unload_module(self, *, req_id, child_id, peer_id=None):
        self.sent.append(('unload', req_id, child_id))


class _Concrete(Routine):
    async def run(self, kwargs):
        pass


class TestLoadUnloadGuard(unittest.IsolatedAsyncioTestCase):

    def _make_ctx(self, started: bool):
        instance = _Concrete()
        stub = _StubIO()
        ctx = RunContext(id='1', name='c', peer_id='p', io=stub, routine=instance,
                         runtime=stub.runtime, transport=None)
        instance._active_ctx = ctx
        instance._started = started
        return instance, stub, ctx

    async def test_load_rejected_when_not_started(self):
        """_started=False:load_module() 抛 RuntimeError,不发 wire."""
        _i, stub, ctx = self._make_ctx(started=False)
        with self.assertRaises(RuntimeError):
            await ctx.load_module('root', 'child')
        self.assertEqual(stub.sent, [], '未 started 不应发出 routine.load_module')

    async def test_unload_rejected_when_not_started(self):
        """_started=False:unload_module() 抛 RuntimeError,不发 wire."""
        _i, stub, ctx = self._make_ctx(started=False)
        with self.assertRaises(RuntimeError):
            await ctx.unload_module('child')
        self.assertEqual(stub.sent, [], '未 started 不应发出 routine.unload_module')

    async def test_load_sends_wire_and_waits_ack(self):
        """_started=True:发 routine.load_module 并等 module_loaded ack."""
        _i, stub, ctx = self._make_ctx(started=True)

        async def ack_after_send():
            await asyncio.sleep(0.01)
            req_id = stub.sent[0][1]
            fut = stub.runtime.pop_load_future(req_id)
            self.assertIsNotNone(fut, 'load 应注册 future')
            fut.set_result(None)

        asyncio.create_task(ack_after_send())
        await ctx.load_module('root', 'newmod', name='新模块')
        self.assertEqual(len(stub.sent), 1)
        self.assertEqual(stub.sent[0][0], 'load')
        self.assertEqual(stub.sent[0][2:4], ('root', 'newmod'))
        self.assertEqual(stub.sent[0][4], '新模块')  # name 透传

    async def test_load_error_raises(self):
        """module_loaded ok=false(child 已存在)-> load_module() 抛 LoadModuleError."""
        _i, stub, ctx = self._make_ctx(started=True)

        async def reject_after_send():
            await asyncio.sleep(0.01)
            req_id = stub.sent[0][1]
            fut = stub.runtime.pop_load_future(req_id)
            fut.set_exception(LoadModuleError('module "newmod" already exists'))

        asyncio.create_task(reject_after_send())
        with self.assertRaises(LoadModuleError):
            await ctx.load_module('root', 'newmod')

    async def test_unload_sends_wire_and_waits_ack(self):
        """_started=True:发 routine.unload_module 并等 module_unloaded ack."""
        _i, stub, ctx = self._make_ctx(started=True)

        async def ack_after_send():
            await asyncio.sleep(0.01)
            req_id = stub.sent[0][1]
            fut = stub.runtime.pop_unload_future(req_id)
            self.assertIsNotNone(fut, 'unload 应注册 future')
            fut.set_result(None)

        asyncio.create_task(ack_after_send())
        await ctx.unload_module('child')
        self.assertEqual(stub.sent[0][0], 'unload')

    async def test_unload_error_raises(self):
        """module_unloaded ok=false(有子)-> unload_module() 抛 UnloadModuleError."""
        _i, stub, ctx = self._make_ctx(started=True)

        async def reject_after_send():
            await asyncio.sleep(0.01)
            req_id = stub.sent[0][1]
            fut = stub.runtime.pop_unload_future(req_id)
            fut.set_exception(UnloadModuleError('module "child" has children'))

        asyncio.create_task(reject_after_send())
        with self.assertRaises(UnloadModuleError):
            await ctx.unload_module('child')


# ===== Part 2: dial-out 往返(gRPC fake kernel)=====

class LoadRunner(Routine):
    """run 里 load 一个带 name 的子模块,get_module_tree 主动拉刷新,验 name_of + conflict,再 unload."""

    async def run(self, kwargs):
        parent = kwargs.get('parent', 'figure')
        child = kwargs.get('child', 'dynamic')
        name = kwargs.get('name', '动态模块')
        await self.load_module(parent, child, name)
        # load ack 后 kernel 重推 module.tree;主动拉一次确保 cache 刷新(避开 push 竞速).
        tree = await self.get_module_tree()
        has_child = child in tree._parents if tree else False
        conflicts = tree.conflict([parent], [child]) if tree else False
        child_name = tree.name_of(child) if tree else None
        await self.unload_module(child)
        return {'has_child': has_child, 'conflicts': conflicts, 'child_name': child_name}


BASE_TREE = {'root': 'root', 'modules': {
    'root': {'children': ['figure']},
    'figure': {},
}}


class _FakeKernel:
    """gRPC bidi Stream client,模拟 kernel:
    - routine.load_module -> 回 module_loaded{ok:true} + 重推 module.tree(含 child)
    - routine.unload_module -> 回 module_unloaded{ok:true} + 重推 module.tree(不含 child)
    - routine.get_module_tree -> 回 get_module_tree_reply{ok,tree}
    其它事件入队供断言.
    """

    def __init__(self, addr, base_tree):
        self.channel = grpc.aio.insecure_channel(addr)
        self.stub = routine_pb2_grpc.RoutineServiceStub(self.channel)
        self._call = self.stub.Stream()
        self._base_tree = base_tree
        self._extra = {}  # 动态加载的 child -> {'parent': parent_id, 'name': name}
        self.events: asyncio.Queue = asyncio.Queue()
        self._reader = asyncio.create_task(self._read())

    def _current_tree(self):
        # base 拷贝(children 列表 load/unload 会增删,须拷列表不能就地改 base)
        modules = {m: {**rec, 'children': list(rec.get('children', []))}
                   for m, rec in self._base_tree['modules'].items()}
        for child, extra in self._extra.items():
            parent = extra['parent']
            modules.setdefault(parent, {}).setdefault('children', []).append(child)
            rec = {}
            if extra.get('name'):
                rec['name'] = extra['name']
            modules[child] = rec
        return {'root': self._base_tree['root'], 'modules': modules}

    async def _read(self):
        try:
            async for msg in self._call:
                d = MessageToDict(msg)
                ev = d.get('event', '')
                if ev == ROUTINE_LOAD_MODULE:
                    req_id = d.get('req_id', '')
                    parent = d.get('parent_id', '')
                    child = d.get('child_id', '')
                    name = d.get('name', '')
                    self._extra[child] = {'parent': parent, 'name': name}
                    await self._write({'event': ROUTINE_MODULE_LOADED, 'req_id': req_id, 'ok': True})
                    await self._write({'event': MODULE_TREE, 'tree': self._current_tree()})
                elif ev == ROUTINE_UNLOAD_MODULE:
                    req_id = d.get('req_id', '')
                    child = d.get('child_id', '')
                    self._extra.pop(child, None)
                    await self._write({'event': ROUTINE_MODULE_UNLOADED, 'req_id': req_id, 'ok': True})
                    await self._write({'event': MODULE_TREE, 'tree': self._current_tree()})
                elif ev == ROUTINE_GET_MODULE_TREE:
                    req_id = d.get('req_id', '')
                    await self._write({'event': ROUTINE_GET_MODULE_TREE_REPLY, 'req_id': req_id,
                                       'ok': True, 'tree': self._current_tree()})
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
        await self._write({'event': LIFECYCLE_START, 'id': id, 'name': name})

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


class TestLoadUnloadDialout(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rs = Routines()
        self.rs.register(LoadRunner)
        self.transport = GrpcServerTransport('127.0.0.1:0')
        self.server = RoutineHub(self.rs, transport=self.transport, hub_id='t')
        self.transport.attach(self.server)
        await self.transport.start()
        self.client = _FakeKernel(f'127.0.0.1:{self.transport.bound_port}', BASE_TREE)

    async def asyncTearDown(self):
        try:
            await self.client.close()
        except Exception:
            pass
        await self.transport.stop()

    async def test_load_then_unload_roundtrip(self):
        """dial-out:load 往返 + tree 刷新 + conflict 覆盖新子模块,再 unload."""
        await self.client.create('1', 'load_runner', kwargs={'parent': 'figure', 'child': 'dynamic'})
        await self.client.start('1', 'load_runner')
        stopped = await self.client.recv(
            lambda m: m.get('event') == 'lifecycle.stopped' and m.get('id') == '1',
        )
        result = stopped.get('result', {})
        # load 后 tree 含 dynamic(挂 figure 下)
        self.assertTrue(result.get('has_child'), 'load 后 tree 应含新子模块 dynamic')
        # figure ↔ dynamic 冲突(dynamic 挂 figure 下,cone 相交)
        self.assertTrue(result.get('conflicts'), 'load 后 conflict 应覆盖新子模块')
        # name 透传:name_of(child) == 传入的 name
        self.assertEqual(result.get('child_name'), '动态模块')


if __name__ == '__main__':
    unittest.main(verbosity=2)
