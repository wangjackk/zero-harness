"""ModuleTree ---- hub 侧模块树拓扑缓存,业务侧编排策略算 cone/conflict 用.

kernel 经 ``module.tree`` 事件推拓扑(catalog push / load/unload 后变更推送),
本模块据此重建本地缓存,算 cone(祖先+自己+后代)和 conflict----跟 kernel 侧
``Tree.try_acquire`` 用**同一个 cone 语义**.

业务侧编排策略(如自动串并行)用 ``conflict(mods_a, mods_b)`` 静态判定两组
modules 是否冲突,据此分组(冲突→串行,否则→并行).modules 由调用方传入
(编排器用父 handle.modules,实例级----created 回报带回,单一真理源).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class ModuleTree:
    """模块树拓扑缓存:cone 算用.构造:``ModuleTree.from_dict(tree_payload)``."""

    def __init__(self, root_id: str,
                 parents: Dict[str, Optional[str]],
                 children: Dict[str, List[str]],
                 names: Optional[Dict[str, str]] = None):
        self.root_id = root_id
        self._parents = parents
        self._children = children
        self._names: Dict[str, str] = names or {}
        self._cone_cache: Dict[str, frozenset] = {}

    @classmethod
    def from_dict(cls, tree_payload: Dict[str, Any]) -> 'ModuleTree':
        """从 kernel 推来的 flat 拓扑 ``{"root": id, "modules": {id: {"children": [...]}}}``
        构造视图(与 kernel ``Tree.serialize`` 输出同构)."""
        if not isinstance(tree_payload, dict) or 'modules' not in tree_payload:
            raise ValueError(f'invalid module tree payload: {tree_payload!r}')
        modules = tree_payload['modules']
        if not isinstance(modules, dict):
            raise ValueError(f'invalid modules map: {modules!r}')
        parents: Dict[str, Optional[str]] = {}
        children: Dict[str, List[str]] = {}
        names: Dict[str, str] = {}
        for mid, rec in modules.items():
            nm = mid
            ch: List[str] = []
            if isinstance(rec, dict):
                nm = rec.get('name') or mid
                ch = list(rec.get('children') or [])
            parents[mid] = None
            names[mid] = nm
            children[mid] = ch
        for pid, chlist in children.items():
            for c in chlist:
                parents[c] = pid
        root_id = tree_payload.get('root') or 'root'
        if root_id not in parents:
            raise ValueError(
                f'root {root_id!r} not in module tree payload: {tree_payload!r}')
        return cls(root_id, parents, children, names)

    def cone(self, module: str) -> frozenset:
        """module 的冲突锥:祖先 + 自己 + 后代.不在树里返回空集."""
        cached = self._cone_cache.get(module)
        if cached is not None:
            return cached
        if module not in self._parents:
            return frozenset()
        out: set = set()
        p = self._parents[module]
        while p is not None:
            out.add(p)
            p = self._parents.get(p)
        out.add(module)
        stack = list(self._children.get(module, []))
        while stack:
            c = stack.pop()
            out.add(c)
            stack.extend(self._children.get(c, []))
        cone = frozenset(out)
        self._cone_cache[module] = cone
        return cone

    def conflict(self, mods_a: List[str], mods_b: List[str]) -> bool:
        """两组 modules 是否冲突:任一对的 cone 相交即冲突.

        ``conflict(a, b)=False`` 意味着(无竞态时)a,b 能并行 acquire 不撞;
        ``=True`` 意味着并行必有一个被拒,业务侧应串行化.空集(任一为空)视为
        无冲突;未知模块名视为无冲突(保守放行,caller 负责保证模块名合法).
        """
        if not mods_a or not mods_b:
            return False
        a_cone: set = set()
        for m in mods_a:
            a_cone |= self.cone(m)
        for m in mods_b:
            if m in a_cone:
                return True
        return False

    def name_of(self, module_id: str) -> str:
        """模块显示名(缺省 = id).name 可重复,conflict/cone 用 id(唯一 key)."""
        return self._names.get(module_id, module_id)

    def __repr__(self) -> str:
        return f'ModuleTree(root={self.root_id!r}, {len(self._parents)} nodes)'
