"""ModuleTree 纯函数测试:cone / conflict / from_dict.

验证:
  1. from_dict 构造 + 拓扑正确(parents/children).
  2. cone = 祖先 + 自己 + 后代(body cone = {root,figure,body,leg}).
  3. conflict 语义:一个模块在另一个的 cone 内才冲突.
     - body↔leg 冲突(leg 在 body cone 内)
     - output↔body 不冲突(output 不在 body cone,反之亦然)
     - mouth↔head 不冲突(不同子树,只共享 root----但 holder 只 tag 声明节点,
       cone 共享 root 不等于冲突)
     - root↔任意 冲突(root 是所有节点的祖先,在所有 cone 内)
  4. 空集无冲突;未知模块当空集(不抛).
  5. cone 缓存(重复调用同 module 返回同一 frozenset).

跟 kernel module.TestTryAcquireAndRelease 的 cone 语义对齐:TryAcquire 检查
cone 内节点的 holders,holder 只 tag 声明节点.conflict = "一个模块在另一个的
cone 内"----不是 "cone 交集非空"(那是错的:共享 root 不算冲突).
"""
import unittest

from routine.module_tree import ModuleTree


def tree4() -> ModuleTree:
    """对标 kernel module.tree4:root{figure{head,body{leg}}, core{mouth}, output}."""
    return ModuleTree.from_dict({
        'root': 'root',
        'modules': {
            'root': {'children': ['figure', 'core', 'output']},
            'figure': {'children': ['head', 'body']},
            'head': {},
            'body': {'children': ['leg']},
            'leg': {},
            'core': {'children': ['mouth']},
            'mouth': {},
            'output': {},
        },
    })


class TestModuleTree(unittest.TestCase):

    def test_from_dict_builds_topology(self):
        t = tree4()
        self.assertEqual(t.root_id, 'root')
        # root + figure/head/body/leg + core/mouth + output = 8 节点
        self.assertEqual(len(t._parents), 8)
        self.assertIsNone(t._parents['root'])
        self.assertEqual(t._parents['body'], 'figure')
        self.assertEqual(t._parents['leg'], 'body')
        self.assertEqual(t._parents['output'], 'root')
        self.assertEqual(t._children['root'], ['figure', 'core', 'output'])
        self.assertEqual(t._children['body'], ['leg'])
        self.assertEqual(t._children['output'], [])

    def test_cone_ancestors_self_descendants(self):
        t = tree4()
        self.assertEqual(t.cone('body'), frozenset({'root', 'figure', 'body', 'leg'}))
        self.assertEqual(t.cone('leg'), frozenset({'root', 'figure', 'body', 'leg'}))
        self.assertEqual(t.cone('output'), frozenset({'root', 'output'}))
        self.assertEqual(t.cone('root'), frozenset({'root', 'figure', 'head', 'body', 'leg', 'core', 'mouth', 'output'}))
        self.assertEqual(t.cone('mouth'), frozenset({'root', 'core', 'mouth'}))

    def test_cone_cached(self):
        t = tree4()
        c1 = t.cone('body')
        c2 = t.cone('body')
        self.assertIs(c1, c2)  # 同一 frozenset 对象(缓存命中)

    def test_cone_unknown_module_empty(self):
        t = tree4()
        self.assertEqual(t.cone('nonexistent'), frozenset())

    def test_conflict_one_in_others_cone(self):
        t = tree4()
        # body ↔ leg:leg 在 body cone 内 → 冲突
        self.assertTrue(t.conflict(['body'], ['leg']))
        self.assertTrue(t.conflict(['leg'], ['body']))  # 对称
        # body ↔ body(同模块)→ 冲突
        self.assertTrue(t.conflict(['body'], ['body']))
        # root ↔ 任意 → 冲突(root 在所有 cone 内)
        self.assertTrue(t.conflict(['root'], ['mouth']))
        self.assertTrue(t.conflict(['root'], ['output']))
        # figure ↔ head:head 在 figure cone 内 → 冲突
        self.assertTrue(t.conflict(['figure'], ['head']))

    def test_conflict_shared_root_not_conflict(self):
        """共享 root 不算冲突----holder 只 tag 声明节点,不 tag cone 节点.

        output 和 body 都以 root 为祖先(cone 都含 root),但 output 不在 body
        的 cone 内,body 不在 output 的 cone 内 → 不冲突.这跟 kernel TryAcquire
        一致:A 占 output 只 tag output 节点,B 占 body 查 cone(body) 不含 output.
        """
        t = tree4()
        self.assertFalse(t.conflict(['output'], ['body']))
        self.assertFalse(t.conflict(['mouth'], ['head']))
        self.assertFalse(t.conflict(['mouth'], ['output']))
        self.assertFalse(t.conflict(['head'], ['leg']))  # head 和 leg 都在 figure 下,但不在彼此 cone

    def test_conflict_empty_and_unknown(self):
        t = tree4()
        # 空集:无冲突(不占模块的 routine 跟谁都无冲突)
        self.assertFalse(t.conflict([], ['body']))
        self.assertFalse(t.conflict(['body'], []))
        self.assertFalse(t.conflict([], []))
        # 未知模块:当空集(不抛;caller 负责保证模块名合法)
        self.assertFalse(t.conflict(['nonexistent'], ['body']))
        self.assertFalse(t.conflict(['body'], ['nonexistent']))

    def test_conflict_multi_modules(self):
        """多模块:任一对冲突即整体冲突(并集语义)."""
        t = tree4()
        # a=[output, mouth], b=[head]:head 跟 mouth 都在 root 下但不在彼此 cone → 不冲突
        self.assertFalse(t.conflict(['output', 'mouth'], ['head']))
        # a=[output, mouth], b=[head, leg]:mouth 跟 leg? 不在彼此 cone;output 跟 leg?不在.
        # 但 leg 跟 body 同 cone----这里没 body,只有 output/mouth vs head/leg → 不冲突
        self.assertFalse(t.conflict(['output', 'mouth'], ['head', 'leg']))
        # a=[output, body], b=[leg]:body 在 leg cone 内 → 冲突
        self.assertTrue(t.conflict(['output', 'body'], ['leg']))

    def test_from_dict_invalid_payload(self):
        with self.assertRaises(ValueError):
            ModuleTree.from_dict({})  # 缺 modules
        with self.assertRaises(ValueError):
            ModuleTree.from_dict({'modules': 'not a dict'})
        with self.assertRaises(ValueError):
            ModuleTree.from_dict({'modules': {}})  # 无 root


    def test_name_of_repeatable_default_id(self):
        """name 可重复(左右手都有大拇指),id 唯一;缺省 name=id;未知返 id 自身."""
        t = ModuleTree.from_dict({
            'root': 'root',
            'modules': {
                'root': {'children': ['left_thumb', 'right_thumb', 'figure']},
                'left_thumb': {'name': '大拇指'},
                'right_thumb': {'name': '大拇指'},
                'figure': {},  # 无 name -> name=id
            },
        })
        self.assertEqual(t.name_of('left_thumb'), '大拇指')
        self.assertEqual(t.name_of('right_thumb'), '大拇指')  # 重复 name
        self.assertEqual(t.name_of('figure'), 'figure')  # 缺省=id
        self.assertEqual(t.name_of('root'), 'root')
        self.assertEqual(t.name_of('unknown'), 'unknown')  # 未知返 id 自身


if __name__ == '__main__':
    unittest.main()
