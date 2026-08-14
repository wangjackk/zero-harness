"""运行时动态注册/重载/移除 routine 验证.

场景: agent 启动时为自己的 skill 操作动态注册 per-agent routine,
agent 销毁时移除, 避免全局 routine 表累积失效类.

验证:
1. Routines.register 注册动态生成的类
2. Routines.get_routine / get_routines 能查到
3. Routines.deregister 移除, 返回被移除的类
4. 移除不存在的 routine 返回 None (不报错)
5. 同名覆盖 (重新注册同名, 新类替换旧类)----本地 Routines.register 语义
6. 动态生成的类能正常实例化 + run
7. RoutineHub.register_routine/reload_routine/deregister_routine async + kernel ack:
   - 无 transport 直接本地(降级)
   - register: 有 transport 发 catalog.register{req_id} 等回执 ok=true 才本地 register
   - register: ok=false(同名冲突, 不区分 conn)抛 RegisterError, 本地不 register
   - reload: 有 transport 发 catalog.reload{req_id} 等回执 ok=true 才本地 register(覆盖)
   - reload: 不区分 conn, 同名总 ok=true(覆盖语义)
   - deregister: 等回执 ok=true 才本地 deregister
"""
import asyncio
import unittest
from typing import Any, ClassVar, Dict

from routine.routine import Routine, Routines
from routine.errors import DeregisterError, RegisterError, ReloadError


class _BaseSkillRoutine(Routine):
    """测试用 routine 基类, 提供 run 实现."""
    meta: ClassVar[Dict[str, Any]] = {}

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return {'ok': True, 'name': self.name}


def _make_dynamic_routine(routine_name: str) -> type:
    """动态生成一个 routine 类, name 在类定义时绑定 (显式覆盖 __init_subclass__)."""
    class _DynamicRoutine(_BaseSkillRoutine):
        name = routine_name   # 显式覆盖, 不走 __init_subclass__ 的蛇形

    return _DynamicRoutine


class TestRuntimeRoutineRegister(unittest.IsolatedAsyncioTestCase):

    def test_register_and_lookup(self):
        """注册后能通过 get_routine / get_routines 查到."""
        reg = Routines()
        DynA = _make_dynamic_routine('agent_a/list_skills')
        reg.register(DynA)

        self.assertEqual(len(reg.get_routines()), 1)
        self.assertIs(reg.get_routine('agent_a/list_skills'), DynA)
        self.assertEqual(reg.get_routine('agent_a/list_skills').name,
                         'agent_a/list_skills')

    def test_register_multiple_agents(self):
        """多个 agent 各自注册, 互不干扰, 都能查到."""
        reg = Routines()
        DynA = _make_dynamic_routine('agent_a/list_skills')
        DynB = _make_dynamic_routine('agent_b/list_skills')
        reg.register(DynA, DynB)

        names = sorted(cls.name for cls in reg.get_routines())
        self.assertEqual(names, ['agent_a/list_skills', 'agent_b/list_skills'])
        self.assertIs(reg.get_routine('agent_a/list_skills'), DynA)
        self.assertIs(reg.get_routine('agent_b/list_skills'), DynB)

    def test_deregister_routine(self):
        """移除指定 routine, 返回被移除的类, 之后查不到."""
        reg = Routines()
        DynA = _make_dynamic_routine('agent_a/list_skills')
        DynB = _make_dynamic_routine('agent_b/list_skills')
        reg.register(DynA, DynB)

        removed = reg.deregister('agent_a/list_skills')

        self.assertIs(removed, DynA)
        self.assertIsNone(reg.get_routine('agent_a/list_skills'))
        self.assertIs(reg.get_routine('agent_b/list_skills'), DynB)
        self.assertEqual(len(reg.get_routines()), 1)

    def test_deregister_nonexistent_returns_none(self):
        """移除不存在的 routine 返回 None, 不报错."""
        reg = Routines()
        removed = reg.deregister('not_exists')
        self.assertIsNone(removed)

    def test_same_name_overwrites(self):
        """同名覆盖: 重新注册同名, 新类替换旧类."""
        reg = Routines()
        DynB1 = _make_dynamic_routine('agent_b/list_skills')
        reg.register(DynB1)

        DynB2 = _make_dynamic_routine('agent_b/list_skills')
        reg.register(DynB2)

        self.assertIs(reg.get_routine('agent_b/list_skills'), DynB2)
        self.assertEqual(len(reg.get_routines()), 1)

    async def test_dynamic_routine_instantiate_and_run(self):
        """动态生成的类能正常实例化 + 调用 run, name 正确."""
        Dyn = _make_dynamic_routine('agent_x/load_skill')
        inst = Dyn()
        result = await inst.run({})
        self.assertEqual(result, {'ok': True, 'name': 'agent_x/load_skill'})

    def test_register_then_deregister_all(self):
        """注册一批, 然后全部移除, 注册表回到空."""
        reg = Routines()
        names = [f'agent_a/{op}' for op in
                 ('list_skills', 'load_skill', 'install_skill',
                  'uninstall_skill', 'search_skill')]
        for n in names:
            reg.register(_make_dynamic_routine(n))
        self.assertEqual(len(reg.get_routines()), 5)

        for n in names:
            removed = reg.deregister(n)
            self.assertIsNotNone(removed)
            self.assertEqual(removed.name, n)

        self.assertEqual(len(reg.get_routines()), 0)

    def test_deregister_idempotent(self):
        """重复移除同一个 routine: 第一次返回类, 后续返回 None."""
        reg = Routines()
        Dyn = _make_dynamic_routine('agent_a/list_skills')
        reg.register(Dyn)

        self.assertIs(reg.deregister('agent_a/list_skills'), Dyn)
        self.assertIsNone(reg.deregister('agent_a/list_skills'))
        self.assertIsNone(reg.deregister('agent_a/list_skills'))


class _AckFakeTransport:
    """模拟 kernel transport:记录出站消息,按需注入 catalog.registered/deregistered 回执.

    用法:
    - sent: 记录所有 send_event 的 payload(测试断言用)
    - auto_ack: True 时自动回执 ok=True(走 on_inbound 模拟 kernel 收到后回执)
    - pending_acks: 手动控制回执(测试 fail 场景)
    """

    def __init__(self, hub, auto_ack=True):
        self.sent = []
        self.hub = hub
        self.auto_ack = auto_ack
        # req_id -> (ok, error) 手动注入回执;auto_ack=False 时用
        self.manual_acks = {}

    async def send_event(self, payload, peer_id=None):
        self.sent.append(payload)
        if not self.auto_ack:
            return
        # 自动回执(模拟 kernel):
        # catalog.register → catalog.registered{ok=true}
        # catalog.reload   → catalog.reloaded{ok=true}
        # catalog.deregister → 两跳:发 cmd 回持有者 → 持有者发 cmd.ack → 发 deregistered
        # catalog.deregister.cmd.ack → 触发 deregistered(两跳的第二跳回执)
        event = payload.get('event', '')
        req_id = payload.get('req_id', '')
        if not req_id:
            return
        if event == 'catalog.register':
            await self.hub.on_inbound({
                'event': 'catalog.registered', 'req_id': req_id, 'ok': True,
            })
        elif event == 'catalog.reload':
            await self.hub.on_inbound({
                'event': 'catalog.reloaded', 'req_id': req_id, 'ok': True,
            })
        elif event == 'catalog.deregister':
            # 两跳:模拟 kernel 发 cmd 回给 hub(持有者==请求者)
            name = payload.get('name', '')
            await self.hub.on_inbound({
                'event': 'catalog.deregister.cmd', 'req_id': req_id, 'name': name,
            })
            # cmd handler 会发 cmd.ack,send_event 递归处理 cmd.ack → 发 deregistered
        elif event == 'catalog.deregister.cmd.ack':
            # cmd.ack 的回执:模拟 kernel 发 deregistered 给请求者
            ok = payload.get('ok', False)
            await self.hub.on_inbound({
                'event': 'catalog.deregistered', 'req_id': req_id, 'ok': ok,
            })

    async def inject_ack(self, req_id, ok, error=None):
        """手动注入回执(auto_ack=False 时用).

        deregister 直接注入 deregistered(跳过两跳,模拟 kernel 拒绝 name 不存在).
        """
        msg = {'req_id': req_id, 'ok': ok}
        if error:
            msg['error'] = error
        # 判断是 register / reload / deregister(从 sent 里找)
        for p in self.sent:
            if p.get('req_id') == req_id:
                ev = p.get('event', '')
                if ev == 'catalog.register':
                    msg['event'] = 'catalog.registered'
                elif ev == 'catalog.reload':
                    msg['event'] = 'catalog.reloaded'
                elif ev == 'catalog.deregister':
                    msg['event'] = 'catalog.deregistered'
                break
        await self.hub.on_inbound(msg)


class TestRoutineHubRegisterEvents(unittest.IsolatedAsyncioTestCase):
    """RoutineHub.register_routine/reload_routine/deregister_routine async + kernel ack 验证.

    新机制: kernel 是唯一真理源. 三层语义:
    - register: 同名一律 fail(不区分 conn). py 发 catalog.register{req_id} 等回执
      ok=true 才本地 register; ok=false(同名冲突)抛 RegisterError, 本地不 register.
    - reload: 不区分 conn, 同名覆盖. py 发 catalog.reload{req_id} 等回执 ok=true
      才本地 register(覆盖); ok=false(罕见----name 为空等参数错)抛 ReloadError.
    - deregister: 等回执 ok=true 才本地 deregister.
    """

    def _make_server(self, transport):
        from routine.server import RoutineHub
        return RoutineHub(Routines(), transport=transport, hub_id='t')

    async def test_register_routine_transport_none_local_only(self):
        """transport=None 时 register_routine 直接本地注册, 不发事件也不报错."""
        srv = self._make_server(transport=None)
        Dyn = _make_dynamic_routine('agent_a/list_skills')
        await srv.register_routine(Dyn)
        # 本地能查到
        self.assertIs(srv.runtime.routines.get_routine('agent_a/list_skills'), Dyn)

    async def test_register_routine_sends_catalog_register_and_waits_ack(self):
        """transport 存在时:发 catalog.register{req_id} 等回执 ok=true 才本地 register."""
        srv = self._make_server(transport=None)  # 占位,后面替换
        transport = _AckFakeTransport(srv, auto_ack=True)
        srv.transport = transport

        DynA = _make_dynamic_routine('agent_a/list_skills')
        DynB = _make_dynamic_routine('agent_a/load_skill')
        await srv.register_routine(DynA, DynB)

        # 发了 2 条 catalog.register(各带 req_id)
        adds = [p for p in transport.sent if p.get('event') == 'catalog.register']
        self.assertEqual(len(adds), 2, f'expected 2 catalog.register, got: {transport.sent}')
        names = sorted(p['name'] for p in adds)
        self.assertEqual(names, ['agent_a/list_skills', 'agent_a/load_skill'])
        # payload 带 req_id + is_passive + meta
        for p in adds:
            self.assertIn('req_id', p)
            self.assertIn('is_passive', p)
            self.assertIn('meta', p)
        # 本地已 register(等回执后)
        self.assertIs(srv.runtime.routines.get_routine('agent_a/list_skills'), DynA)
        self.assertIs(srv.runtime.routines.get_routine('agent_a/load_skill'), DynB)
        # 不应发 catalog.push / catalog.deregister
        self.assertFalse(any(p.get('event') == 'catalog.push' for p in transport.sent))
        self.assertFalse(any(p.get('event') == 'catalog.deregister' for p in transport.sent))

    async def test_register_routine_ack_fail_raises_and_no_local_register(self):
        """kernel 回执 ok=false(同名冲突)→ 抛 RegisterError, 本地不 register."""
        srv = self._make_server(transport=None)
        transport = _AckFakeTransport(srv, auto_ack=False)
        srv.transport = transport

        Dyn = _make_dynamic_routine('agent_a/list_skills')

        # 手动注入 ok=false 回执:需要在 send_event 后立即注入(竞态)
        # 用一个 task 模拟 kernel 回执
        async def _delayed_reject():
            await asyncio.sleep(0.01)
            # 从 sent 里拿最新 req_id
            for p in transport.sent:
                if p.get('event') == 'catalog.register':
                    req_id = p['req_id']
                    await transport.inject_ack(req_id, ok=False,
                                               error='name already exists')
                    return
        asyncio.create_task(_delayed_reject())

        with self.assertRaises(RegisterError) as ctx:
            await srv.register_routine(Dyn)
        self.assertIn('already exists', str(ctx.exception).lower())
        # 本地未 register(kernel 拒绝)
        self.assertIsNone(srv.runtime.routines.get_routine('agent_a/list_skills'))

    async def test_register_routine_same_name_fails(self):
        """register_routine 同名一律 fail(不区分 conn).本地已有的再 register 会失败.

        新语义: register 同名 fail(无论同 conn 还是跨 conn). 覆盖走 reload_routine.
        本测试: 本地先有 DynA(降级注册), 再 register_routine(DynA) 时 kernel 回执
        ok=false(同名冲突), 抛 RegisterError, 本地保留旧类不变.
        """
        srv = self._make_server(transport=None)
        transport = _AckFakeTransport(srv, auto_ack=False)
        srv.transport = transport

        DynA = _make_dynamic_routine('agent_a/list_skills')
        # 先本地 register(不走 register_routine, 不发事件)
        srv.runtime.routines.register(DynA)

        # 手动注入 ok=false(同名冲突)
        async def _delayed_reject():
            await asyncio.sleep(0.01)
            for p in transport.sent:
                if p.get('event') == 'catalog.register':
                    await transport.inject_ack(p['req_id'], ok=False,
                                               error='name already exists')
                    return
        asyncio.create_task(_delayed_reject())

        with self.assertRaises(RegisterError):
            await srv.register_routine(DynA)
        # 本地仍是旧类(kernel 拒绝,本地不动)
        self.assertIs(srv.runtime.routines.get_routine('agent_a/list_skills'), DynA)

    async def test_deregister_routine_sends_and_waits_ack(self):
        """deregister_routine 两跳:发 catalog.deregister → kernel 发 cmd → 本地 dereg +
        发 cmd.ack → kernel 发 deregistered ok=true → resolve(返回被移除的类)."""
        srv = self._make_server(transport=None)
        transport = _AckFakeTransport(srv, auto_ack=True)
        srv.transport = transport

        Dyn = _make_dynamic_routine('agent_a/list_skills')
        # 先 register_routine 让本地有这个 routine
        await srv.register_routine(Dyn)
        transport.sent.clear()

        removed = await srv.deregister_routine('agent_a/list_skills')

        self.assertIs(removed, Dyn)
        # 本地已删(cmd handler 在两跳流程中执行了本地 dereg)
        self.assertIsNone(srv.runtime.routines.get_routine('agent_a/list_skills'))
        # 发了 catalog.deregister{req_id}(请求者→kernel)
        dereg = [p for p in transport.sent if p.get('event') == 'catalog.deregister']
        self.assertEqual(len(dereg), 1)
        self.assertEqual(dereg[0]['name'], 'agent_a/list_skills')
        self.assertIn('req_id', dereg[0])
        # 发了 catalog.deregister.cmd.ack{ok=true}(持有者→kernel,两跳第二跳)
        acks = [p for p in transport.sent if p.get('event') == 'catalog.deregister.cmd.ack']
        self.assertEqual(len(acks), 1)
        self.assertEqual(acks[0]['ok'], True)

    async def test_deregister_routine_ack_fail_raises_and_no_local_delete(self):
        """kernel 回执 ok=false(name 不在 kernel 路由表,跳过两跳直接拒绝)
        → 抛 DeregisterError, 本地不删(cmd handler 未执行)."""
        srv = self._make_server(transport=None)
        transport = _AckFakeTransport(srv, auto_ack=False)
        srv.transport = transport

        Dyn = _make_dynamic_routine('agent_a/list_skills')
        # 本地先有(模拟之前 register 过)
        srv.runtime.routines.register(Dyn)

        async def _delayed_reject():
            await asyncio.sleep(0.01)
            for p in transport.sent:
                if p.get('event') == 'catalog.deregister':
                    req_id = p['req_id']
                    await transport.inject_ack(req_id, ok=False,
                                               error='not registered')
                    return
        asyncio.create_task(_delayed_reject())

        with self.assertRaises(DeregisterError) as ctx:
            await srv.deregister_routine('agent_a/list_skills')
        self.assertIn('not registered', str(ctx.exception).lower())
        # 本地未删(kernel 拒绝,cmd handler 未执行,本地不动)
        self.assertIs(srv.runtime.routines.get_routine('agent_a/list_skills'), Dyn)

    async def test_deregister_nonexistent_transport_none_local_only(self):
        """transport=None 时 deregister_routine 直接本地删, 不发事件也不报错."""
        srv = self._make_server(transport=None)
        Dyn = _make_dynamic_routine('agent_a/list_skills')
        srv.runtime.routines.register(Dyn)

        removed = await srv.deregister_routine('agent_a/list_skills')
        self.assertIs(removed, Dyn)
        self.assertIsNone(srv.runtime.routines.get_routine('agent_a/list_skills'))

    # --- reload_routine 测试(不区分 conn, 同名覆盖) ---

    async def test_reload_routine_transport_none_local_only(self):
        """transport=None 时 reload_routine 直接本地 register(同名覆盖), 不发事件."""
        srv = self._make_server(transport=None)
        Dyn1 = _make_dynamic_routine('agent_a/list_skills')
        srv.runtime.routines.register(Dyn1)

        # reload 同名的新类(Dyn2), 本地覆盖
        Dyn2 = _make_dynamic_routine('agent_a/list_skills')
        await srv.reload_routine(Dyn2)
        self.assertIs(srv.runtime.routines.get_routine('agent_a/list_skills'), Dyn2)

    async def test_reload_routine_sends_catalog_reload_and_waits_ack(self):
        """transport 存在时:发 catalog.reload{req_id} 等回执 ok=true 才本地 register(覆盖)."""
        srv = self._make_server(transport=None)
        transport = _AckFakeTransport(srv, auto_ack=True)
        srv.transport = transport

        Dyn1 = _make_dynamic_routine('agent_a/list_skills')
        srv.runtime.routines.register(Dyn1)  # 先有旧类

        Dyn2 = _make_dynamic_routine('agent_a/list_skills')  # 新类(同名)
        await srv.reload_routine(Dyn2)

        # 发了 catalog.reload(带 req_id)
        reloads = [p for p in transport.sent if p.get('event') == 'catalog.reload']
        self.assertEqual(len(reloads), 1)
        self.assertEqual(reloads[0]['name'], 'agent_a/list_skills')
        self.assertIn('req_id', reloads[0])
        self.assertIn('is_passive', reloads[0])
        self.assertIn('meta', reloads[0])
        # 本地已覆盖(等回执后)
        self.assertIs(srv.runtime.routines.get_routine('agent_a/list_skills'), Dyn2)
        # 不应发 catalog.register / catalog.push / catalog.deregister
        self.assertFalse(any(p.get('event') == 'catalog.register' for p in transport.sent))
        self.assertFalse(any(p.get('event') == 'catalog.push' for p in transport.sent))

    async def test_reload_routine_overwrites_cross_conn(self):
        """reload_routine 不区分 conn, 同名总 ok=true(覆盖语义, 模拟跨 conn 覆盖).

        kernel 侧 ReloadRoutine 不区分 conn 覆盖, 总回执 ok=true. py 收到后本地
        Routines.register(同名覆盖). 不抛 ReloadError.
        """
        srv = self._make_server(transport=None)
        transport = _AckFakeTransport(srv, auto_ack=True)
        srv.transport = transport

        # 模拟: 本地先有旧类(其他 conn 注册的), reload 新类覆盖
        Dyn1 = _make_dynamic_routine('agent_a/list_skills')
        srv.runtime.routines.register(Dyn1)

        Dyn2 = _make_dynamic_routine('agent_a/list_skills')
        # auto_ack=True 模拟 kernel reload 总回执 ok=true(不区分 conn 覆盖)
        await srv.reload_routine(Dyn2)

        # 本地已覆盖为新类
        self.assertIs(srv.runtime.routines.get_routine('agent_a/list_skills'), Dyn2)

    async def test_reload_routine_ack_fail_raises(self):
        """kernel 回执 ok=false(罕见----name 为空等参数错)→ 抛 ReloadError, 本地不动.

        reload 总 ok=true(覆盖语义), ok=false 只发生在参数错场景. 测试模拟该罕见分支.
        """
        srv = self._make_server(transport=None)
        transport = _AckFakeTransport(srv, auto_ack=False)
        srv.transport = transport

        Dyn1 = _make_dynamic_routine('agent_a/list_skills')
        srv.runtime.routines.register(Dyn1)

        Dyn2 = _make_dynamic_routine('agent_a/list_skills')

        async def _delayed_reject():
            await asyncio.sleep(0.01)
            for p in transport.sent:
                if p.get('event') == 'catalog.reload':
                    await transport.inject_ack(p['req_id'], ok=False,
                                               error='name is required')
                    return
        asyncio.create_task(_delayed_reject())

        with self.assertRaises(ReloadError):
            await srv.reload_routine(Dyn2)
        # 本地仍是旧类(kernel 拒绝,本地不动)
        self.assertIs(srv.runtime.routines.get_routine('agent_a/list_skills'), Dyn1)

    async def test_reload_routine_multiple_distinct_names(self):
        """reload_routine 多个不同 name 的 routine: 逐个发 catalog.reload, 都覆盖."""
        srv = self._make_server(transport=None)
        transport = _AckFakeTransport(srv, auto_ack=True)
        srv.transport = transport

        DynA = _make_dynamic_routine('agent_a/list_skills')
        DynB = _make_dynamic_routine('agent_a/load_skill')
        await srv.reload_routine(DynA, DynB)

        reloads = [p for p in transport.sent if p.get('event') == 'catalog.reload']
        self.assertEqual(len(reloads), 2)
        names = sorted(p['name'] for p in reloads)
        self.assertEqual(names, ['agent_a/list_skills', 'agent_a/load_skill'])
        # 本地都 register 了
        self.assertIs(srv.runtime.routines.get_routine('agent_a/list_skills'), DynA)
        self.assertIs(srv.runtime.routines.get_routine('agent_a/load_skill'), DynB)


if __name__ == '__main__':
    unittest.main(verbosity=2)
