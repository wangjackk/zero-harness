"""权威模块树(kernel 侧)---- 移植 Go kernel/module.

cone 语义:占用 m 会挡住 cone(祖先+自己+后代)内任意节点;cone 内任意节点
被占也会挡住 m.TryAcquire check-and-occupy 原子(asyncio 单线程无锁).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set


class ConflictError(Exception):
    """模块被别的 routine 占用导致 Start/acquire 被拒."""

    def __init__(self, requested: str, blocked_by: str, holders: List[int]):
        self.requested = requested
        self.blocked_by = blocked_by
        self.holders = holders
        super().__init__(
            f'module {requested!r} blocked at {blocked_by!r} by routines {holders}')


class _Module:
    __slots__ = ('id', 'name', 'parent', 'children', 'holders')

    def __init__(self, id: str, name: str, parent: Optional[str],
                 children: Optional[List[str]] = None):
        self.id = id
        self.name = name
        self.parent = parent
        self.children: List[str] = children or []
        self.holders: List[int] = []


class Tree:
    """模块树.flat map(id → _Module),parent 由 children 反推."""

    def __init__(self, root_id: str, records: Dict[str, Dict[str, List[str]]]):
        self._root = root_id
        self._modules: Dict[str, _Module] = {}
        for mid, rec in records.items():
            name = rec.get('name') or mid
            self._modules[mid] = _Module(mid, name, None, list(rec.get('children') or []))
        for m in self._modules.values():
            for c in m.children:
                child = self._modules.get(c)
                if child is not None:
                    child.parent = m.id

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> 'Tree':
        """从 ``{"root": id, "modules": {id: {"children": [...], "name": ...}}}`` 构造."""
        modules = payload.get('modules')
        if not isinstance(modules, dict) or not modules:
            raise ValueError(f'invalid module tree payload: {payload!r}')
        root = payload.get('root') or 'root'
        if root not in modules:
            raise ValueError(f'root {root!r} not in modules')
        return cls(root, modules)

    @property
    def root_id(self) -> str:
        return self._root

    def cone(self, module: str) -> Set[str]:
        """module 的冲突锥:祖先 + 自己 + 后代.不存在返回空集."""
        n = self._modules.get(module)
        if n is None:
            return set()
        out = {module}
        p = n.parent
        while p is not None:
            out.add(p)
            p = self._modules[p].parent if p in self._modules else None
        stack = list(n.children)
        while stack:
            c = stack.pop()
            out.add(c)
            m = self._modules.get(c)
            if m is not None:
                stack.extend(m.children)
        return out

    def try_acquire(self, rid: int, modules: List[str],
                    ancestors: Set[int]) -> None:
        """为 rid 占用 modules.冲突抛 ConflictError,缺失模块抛 KeyError.

        ancestors 是 rid 的 routine 树祖先 id 集:cone 内被占节点的 holder
        若全是 rid 自己或其祖先,放行(父子共占);否则冲突.
        """
        # 第一遍 check cone
        for m in modules:
            if m not in self._modules:
                raise KeyError(f'module {m!r} not found in tree')
            for c in self.cone(m):
                for h in self._modules[c].holders:
                    if h == rid or h in ancestors:
                        continue
                    raise ConflictError(m, c, list(self._modules[c].holders))
        # 第二遍占用(去重)
        for m in modules:
            n = self._modules[m]
            if rid not in n.holders:
                n.holders.append(rid)

    def evictable_holders(self, modules: List[str], ancestors: Set[int],
                          rid: int) -> List[int]:
        """cone 内需被 force 驱逐的第三方 holder 集(排除自己/祖先;root 是所有
        routine 的祖先故永不被驱逐).跟 try_acquire 检查完全一致:驱逐后必过."""
        out: Set[int] = set()
        for m in modules:
            if m not in self._modules:
                continue
            for c in self.cone(m):
                for h in self._modules[c].holders:
                    if h == rid or h in ancestors:
                        continue
                    out.add(h)
        return sorted(out)

    def release(self, rid: int) -> None:
        """释放 rid 占用的所有模块(routine 停止时全量清理)."""
        for n in self._modules.values():
            if rid in n.holders:
                n.holders.remove(rid)

    def release_modules(self, rid: int, modules: Iterable[str]) -> None:
        """释放 rid 占用的指定模块(运行时 release)."""
        for m in modules:
            n = self._modules.get(m)
            if n is not None and rid in n.holders:
                n.holders.remove(rid)

    def load_module(self, parent_id: str, child_id: str, name: str = '') -> None:
        """往 parent 下挂 child(动态增拓扑).只挂树不占用."""
        if not child_id:
            raise ValueError('child_id is empty')
        if child_id in self._modules:
            raise ValueError(f'module {child_id!r} already exists')
        p = self._modules.get(parent_id)
        if p is None:
            raise ValueError(f'parent module {parent_id!r} not found')
        self._modules[child_id] = _Module(child_id, name or child_id, parent_id)
        p.children.append(child_id)

    def unload_module(self, child_id: str) -> None:
        """摘掉 child(动态删拓扑).有子/被占报错."""
        n = self._modules.get(child_id)
        if n is None:
            raise ValueError(f'module {child_id!r} not found')
        if n.children:
            raise ValueError(
                f'module {child_id!r} has children {n.children}, unload them first')
        if n.holders:
            raise ValueError(
                f'module {child_id!r} is occupied by routines {n.holders}, release first')
        if n.parent is not None:
            p = self._modules.get(n.parent)
            if p is not None:
                p.children.remove(child_id)
        del self._modules[child_id]

    def serialize(self) -> Dict[str, Dict]:
        """序列化成 flat map(与 Tree.from_dict 输入同构)."""
        mods = {}
        for mid, m in self._modules.items():
            rec: Dict[str, Dict] = {}
            if m.name and m.name != mid:
                rec['name'] = m.name
            if m.children:
                rec['children'] = list(m.children)
            mods[mid] = rec
        return {'root': self._root, 'modules': mods}
