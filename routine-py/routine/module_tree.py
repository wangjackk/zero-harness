"""ModuleTree ---- 模块树拓扑缓存,业务侧编排策略算 cone/conflict 用.

kernel 启动时从 tree.json 加载模块树拓扑;server 连接/断线时 kernel 把拓扑通过
``module.tree`` 事件推过来(变更推送).本模块据此重建本地缓存,算 cone(祖先+自己+
后代)和 conflict----跟 kernel 侧 ``TryAcquire``/``EvictableHolders`` 用**同一个 cone 语义**.

业务侧编排策略(如 AutoSP 自动串并行)用 ``conflict(mods_a, mods_b)`` 静态判定两组
modules 是否冲突,据此分组(冲突→串行,否则→并行).modules 由调用方传入(编排器用
父 ``handle.modules``,实例级----created 回报带回,单一真理源).

跟内核的关系:内核只管正确性不变量(互斥/生命周期/级联 stop);编排策略
(怎么分组,谁等谁,串还是 DAG 边)全在业务侧,DAG/FSM/行为树/自动串并行
各自独立库平起平坐.本类是"自动串并行"策略的纯函数基础.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class ModuleTree:
    """模块树拓扑缓存:cone 算用.

    构造:``ModuleTree.from_dict(tree_payload)``.

    - ``tree_payload`` = kernel ``module.tree`` 事件里的 ``tree`` 字段----
      ``{"root": rootID, "modules": {id: {"children": [...]}}}`` flat map 结构
      (跟 kernel ``tree.json`` / ``module.LoadFile`` 输入同构).

    缓存后 ``cone(m)`` / ``conflict(a, b)`` 都是纯本地计算,零 round-trip.
    """

    def __init__(self, root_id: str,
                 parents: Dict[str, Optional[str]],
                 children: Dict[str, List[str]],
                 names: Optional[Dict[str, str]] = None):
        self.root_id = root_id
        # parents[m] = m 的父模块 id(root 的父是 None).
        self._parents = parents
        # children[m] = m 的直接子模块 id 列表.
        self._children = children
        # names[m] = m 的显示名(可重复,如左右手都有"大拇指");缺省 = id.
        # 渲染/UI 用 name,conflict/cone 仍用 id(唯一 key).
        self._names: Dict[str, str] = names or {}
        # cone 缓存:cone(m) = {祖先...} ∪ {m} ∪ {后代...}.树静态 → 算一次即可.
        self._cone_cache: Dict[str, frozenset[str]] = {}

    @classmethod
    def from_dict(cls, tree_payload: Dict[str, Any]) -> 'ModuleTree':
        """从 kernel 推来的 flat 拓扑构造视图.

        ``tree_payload`` 是 ``{"root": rootID, "modules": {id: {"children": [...]}}}``
        (kernel ``Tree.Serialize`` 输出,flat map keyed by module_id).
        root 固定 id 为 "root"(payload 有 root 字段优先用,否则 fallback 到 "root").
        叶子节点 children 缺省/空.payload 缺 ``modules`` 抛 ValueError.
        """
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
            parents[mid] = None  # 先占位,下面根据 children 反推
            names[mid] = nm
            children[mid] = ch
        # 根据 children 反推 parent
        for pid, chlist in children.items():
            for c in chlist:
                parents[c] = pid
        root_id = tree_payload.get('root') or 'root'
        if root_id not in parents:
            raise ValueError(f'root {root_id!r} not in module tree payload: {tree_payload!r}')
        return cls(root_id, parents, children, names)

    def cone(self, module: str) -> frozenset[str]:
        """返回 module 的冲突锥:祖先 + 自己 + 后代.

        占用 m 会挡住 cone 内任意节点;cone 内任意节点被占也会挡住 m----
        跟 kernel ``(*Node).cone()`` 同语义.module 不在树里返回空集(caller
        自己处理:未知模块视为无冲突或报错,看策略).
        """
        cached = self._cone_cache.get(module)
        if cached is not None:
            return cached
        if module not in self._parents:
            # 未知模块:算空集(不缓存----下次可能同名补进来?树静态其实不会,
            # 但保持 defensive).caller 应在上层校验模块名存在.
            return frozenset()

        out: set[str] = set()
        # 祖先
        p = self._parents[module]
        while p is not None:
            out.add(p)
            p = self._parents.get(p)
        # 自己
        out.add(module)
        # 后代(DFS)
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

        跟 kernel ``TryAcquire`` 的 cone 检查同语义----``conflict(a, b)=False``
        意味着(无竞态时)a,b 能并行 acquire 不撞.``conflict=True`` 意味着
        并行 acquire 必有一个被 ConflictError 拒----业务侧应串行化它们.

        空集(任一为空)视为无冲突:不占模块的 routine 跟谁都无冲突.
        未知模块名(不在树里)视为无冲突----caller 负责保证模块名合法,本方法
        不做模块名校验(跟 kernel ``TryAcquire`` 报 "module not found" 区分:
        那是 acquire 时的硬错,这是静态预测,未知当无冲突保守放行).
        """
        if not mods_a or not mods_b:
            return False
        # 展开 a 的全部 cone 节点,再看 b 任一模块落在其中.
        # cone 是对称的(a.cone ∩ b ≠ ∅ ⟺ b.cone ∩ a ≠ ∅),单方向展开即可.
        a_cone: set[str] = set()
        for m in mods_a:
            a_cone |= self.cone(m)
        for m in mods_b:
            if m in a_cone:
                return True
        return False

    def name_of(self, module_id: str) -> str:
        """模块的显示名(缺省 = id).name 可重复(如左右手都有"大拇指"),id 唯一.

        渲染/UI 用 name;conflict/cone 仍用 id(唯一 key,不可重复).
        未知 module_id 返回 id 自身(caller 负责保证 id 合法).
        """
        return self._names.get(module_id, module_id)

    def __repr__(self) -> str:
        return f'ModuleTree(root={self.root_id!r}, {len(self._parents)} nodes)'
