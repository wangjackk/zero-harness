package module

import (
	"encoding/json"
	"testing"
)

// tree4 测试用模块树(flat map 构造):
//
//	root
//	├─ figure
//	│  ├─ head
//	│  └─ body
//	│     └─ leg
//	└─ core
//	   └─ mouth
func tree4() *Tree {
	return NewTree("root", map[string]ModuleRecord{
		"root":   {Children: []string{"figure", "core"}},
		"figure": {Children: []string{"head", "body"}},
		"head":   {},
		"body":   {Children: []string{"leg"}},
		"leg":    {},
		"core":   {Children: []string{"mouth"}},
		"mouth":  {},
	})
}

func TestTryAcquireAndRelease(t *testing.T) {
	tr := tree4()
	// rid=1 占 body,cone=[figure,body,leg] 外人挡
	if err := tr.TryAcquire(1, []string{"body"}, nil); err != nil {
		t.Fatalf("acquire body: %v", err)
	}
	// 外人 rid=2 占 body -> 冲突
	if err := tr.TryAcquire(2, []string{"body"}, nil); err == nil {
		t.Fatal("外人占 body 应冲突")
	}
	// 外人 rid=2 占 leg(body cone 内)-> 冲突
	if err := tr.TryAcquire(2, []string{"leg"}, nil); err == nil {
		t.Fatal("外人占 leg 应冲突(body 占着)")
	}
	// 释放后外人能占
	tr.Release(1)
	if err := tr.TryAcquire(2, []string{"body"}, nil); err != nil {
		t.Fatalf("释放后外人占 body 应成功: %v", err)
	}
}

func TestParentChildCoshareAncestorSkips(t *testing.T) {
	tr := tree4()
	// 父 rid=1 占 body
	if err := tr.TryAcquire(1, []string{"body"}, nil); err != nil {
		t.Fatalf("父占 body: %v", err)
	}
	// 子 rid=2 也占 body:ancestors={1}(父),cone 检查跳过父 -> 允许共占
	if err := tr.TryAcquire(2, []string{"body"}, map[int]struct{}{1: {}}); err != nil {
		t.Fatalf("子共占 body 应允许(父祖先): %v", err)
	}
	// 外人 rid=3 占 body:holders=[1,2],冲突
	if err := tr.TryAcquire(3, []string{"body"}, nil); err == nil {
		t.Fatal("外人占 body 应冲突(父子共占)")
	}
	// 父 stop 释放,子还占
	tr.Release(1)
	if err := tr.TryAcquire(3, []string{"body"}, nil); err == nil {
		t.Fatal("子还占着 body,外人应冲突")
	}
	// 子 stop 释放,外人能占
	tr.Release(2)
	if err := tr.TryAcquire(3, []string{"body"}, nil); err != nil {
		t.Fatalf("子也释放后外人应能占: %v", err)
	}
}

// TestReleaseClearsRuntimeAcquire 验证关键语义:routine 运行时 acquire 的模块
// (没进 cmd.Modules)在 Release(rid) 全量清理时也被释放--防止泄漏.
// 回归守卫:runRemote defer 无条件 Release(cmd.ID) 就是为这个.
func TestReleaseClearsRuntimeAcquire(t *testing.T) {
	tr := tree4()
	// rid=1 静态占 mouth
	if err := tr.TryAcquire(1, []string{"mouth"}, nil); err != nil {
		t.Fatalf("静态占 mouth: %v", err)
	}
	// rid=1 运行时 acquire body(模拟 start 体里调 acquire,没进 cmd.Modules)
	if err := tr.TryAcquire(1, []string{"body"}, nil); err != nil {
		t.Fatalf("运行时 acquire body: %v", err)
	}
	// Release(1) 全量清理:mouth 和 body 都该释放
	tr.Release(1)
	// 外人 rid=2 占 mouth 应成功(已释放)
	if err := tr.TryAcquire(2, []string{"mouth"}, nil); err != nil {
		t.Fatalf("Release 后 mouth 应释放: %v", err)
	}
	// 外人 rid=2 占 body 应成功(运行时 acquire 的也释放了)
	if err := tr.TryAcquire(2, []string{"body"}, nil); err != nil {
		t.Fatalf("Release 后 body(运行时 acquire)应释放: %v", err)
	}
}

func TestReleaseModulesOnlySpecified(t *testing.T) {
	tr := tree4()
	// rid=1 占 body 和 mouth
	_ = tr.TryAcquire(1, []string{"body"}, nil)
	_ = tr.TryAcquire(1, []string{"mouth"}, nil)
	// ReleaseModules 只释放 body,mouth 还占
	tr.ReleaseModules(1, []string{"body"})
	if err := tr.TryAcquire(2, []string{"body"}, nil); err != nil {
		t.Fatalf("body 应已释放: %v", err)
	}
	if err := tr.TryAcquire(2, []string{"mouth"}, nil); err == nil {
		t.Fatal("mouth 应仍被 rid=1 占着")
	}
}

// TestEvictableHolders 验证 force 驱逐集计算:∪ cone(目标) holders,排除 self/祖先.
// 关键场景:body(非叶,cone 含后代 leg)必须连占 leg 的 routine 一起驱逐--
// "链"(祖先+自己)会漏掉后代,cone 不会.
func TestEvictableHolders(t *testing.T) {
	t.Run("leaf_degenerates_to_chain", func(t *testing.T) {
		// leg 是叶子:cone(leg) = {root, figure, body, leg},但只有 leg 被 rid=3 占.
		// rid=5 force_release(leg):rid=3 在 cone 内且非 self/非祖先 -> 驱逐 [3].
		tr := tree4()
		_ = tr.TryAcquire(3, []string{"leg"}, map[int]struct{}{1: {}})
		got := tr.EvictableHolders([]string{"leg"}, map[int]struct{}{1: {}}, 5)
		if len(got) != 1 || got[0] != 3 {
			t.Fatalf("leg 被 rid=3 占,应驱逐 [3], got %v", got)
		}
	})

	t.Run("nonleaf_catches_descendant_holder", func(t *testing.T) {
		// body 非叶:cone(body)={root,figure,body,leg}.rid=4 只占 leg(body 本身没人占).
		// rid=5 force_release(body):leg 在 body 的 cone 内 -> rid=4 必须被驱逐.
		// "链"(body 的祖先+自己={root,figure,body})会漏掉占 leg 的 4--这是 cone vs 链的关键差别.
		tr := tree4()
		_ = tr.TryAcquire(4, []string{"leg"}, nil)
		got := tr.EvictableHolders([]string{"body"}, nil, 5)
		if len(got) != 1 || got[0] != 4 {
			t.Fatalf("body cone 含 leg,应驱逐占 leg 的 [4], got %v", got)
		}
	})

	t.Run("cosharing_multi_holder", func(t *testing.T) {
		// 父子共占:父 rid=1 占 body,子 rid=2 共占 body(ancestors={1})+ rid=2 占 leg.
		// rid=5 force_release(body)(5 是外人,ancestors 空):cone(body) holders={1,2}
		// 都不是 5 的祖先 -> 都驱逐.验证多 holder 合并去重.
		tr := tree4()
		_ = tr.TryAcquire(1, []string{"body"}, nil)
		_ = tr.TryAcquire(2, []string{"body", "leg"}, map[int]struct{}{1: {}})
		got := tr.EvictableHolders([]string{"body"}, nil, 5)
		if len(got) != 2 {
			t.Fatalf("父子共占 body 应驱逐 [1,2], got %v", got)
		}
		set := map[int]bool{}
		for _, h := range got {
			set[h] = true
		}
		if !set[1] || !set[2] {
			t.Fatalf("应含 1 和 2, got %v", got)
		}
	})

	t.Run("excludes_self_and_ancestors", func(t *testing.T) {
		// 父 rid=1 占 body,子 rid=2 也共占 body(ancestors={1}).
		// rid=2 force_release(body):父 1 在 ancestors -> 排除;自己 2 -> 排除 -> 空.
		// (语义:force 永不驱逐祖先--打断父亲自己也死.)
		tr := tree4()
		_ = tr.TryAcquire(1, []string{"body"}, nil)
		_ = tr.TryAcquire(2, []string{"body"}, map[int]struct{}{1: {}})
		got := tr.EvictableHolders([]string{"body"}, map[int]struct{}{1: {}}, 2)
		if len(got) != 0 {
			t.Fatalf("父祖先和自己都排除,应空, got %v", got)
		}
	})

	t.Run("no_conflict_empty", func(t *testing.T) {
		// 没人占 -> 空(调用方据此跳过驱逐直接 TryAcquire).
		tr := tree4()
		got := tr.EvictableHolders([]string{"body"}, nil, 5)
		if len(got) != 0 {
			t.Fatalf("无冲突应空, got %v", got)
		}
	})
}

// TestAcquireRelations 验证 routine 树四种关系下 acquire 已占模块的行为.
// 设定:routine 树 P(parent) -> A,B(A/B 是 P 的子);A -> C(C 是 A 的子).
// 模块 M 被先 acquire,另一 routine 再 acquire M,看关系决定结果.
func TestAcquireRelations(t *testing.T) {
	t.Run("self_repeat_is_noop", func(t *testing.T) {
		// 自己重复 acquire 同模块:幂等 no-op,holders 不变.
		tr := tree4()
		_ = tr.TryAcquire(1, []string{"body"}, nil)
		// 再 acquire body:rid=1 已持有
		err := tr.TryAcquire(1, []string{"body"}, nil)
		if err != nil {
			t.Fatalf("重复 acquire 应 no-op 成功: %v", err)
		}
		// holders 仍 [1](去重,不重复 append)
		n := tr.modules["body"]
		if len(n.holders) != 1 || n.holders[0] != 1 {
			t.Fatalf("重复 acquire 后 holders 应仍 [1], got %v", n.holders)
		}
	})

	t.Run("child_acquires_parents_held_ok", func(t *testing.T) {
		// 子 acquire 父已占的:子 ancestors 含父 -> 跳过 -> 成功叠加.
		// 父 rid=1 占 body,子 rid=2 acquire body(ancestors={1}).
		tr := tree4()
		_ = tr.TryAcquire(1, []string{"body"}, nil)
		err := tr.TryAcquire(2, []string{"body"}, map[int]struct{}{1: {}})
		if err != nil {
			t.Fatalf("子共占父已占的 body 应成功: %v", err)
		}
		n := tr.modules["body"]
		if len(n.holders) != 2 {
			t.Fatalf("holders 应 [1,2], got %v", n.holders)
		}
	})

	t.Run("parent_acquires_childs_held_conflicts", func(t *testing.T) {
		// 父 acquire 子已占的:父 ancestors 不含子 -> 冲突.
		// 子 rid=2 先占 body(ancestors={1} 成功),父 rid=1 再 acquire body(ancestors={}).
		tr := tree4()
		_ = tr.TryAcquire(2, []string{"body"}, map[int]struct{}{1: {}})
		err := tr.TryAcquire(1, []string{"body"}, nil)
		if err == nil {
			t.Fatal("父 acquire 子已占的 body 应冲突(父不知子已占)")
		}
	})

	t.Run("sibling_conflicts", func(t *testing.T) {
		// 兄弟:A rid=1 占 body,兄弟 B rid=2 acquire body(ancestors={P},P=3 不含 A)-> 冲突.
		tr := tree4()
		_ = tr.TryAcquire(1, []string{"body"}, nil)
		err := tr.TryAcquire(2, []string{"body"}, map[int]struct{}{3: {}})
		if err == nil {
			t.Fatal("兄弟 acquire 同模块应冲突")
		}
	})

	t.Run("cousin_conflicts", func(t *testing.T) {
		// 旁系:无祖先后代关系,同兄弟 -> 冲突.
		tr := tree4()
		_ = tr.TryAcquire(1, []string{"body"}, nil)
		// rid=2 ancestors={99}(跟 rid=1 无关)
		err := tr.TryAcquire(2, []string{"body"}, map[int]struct{}{99: {}})
		if err == nil {
			t.Fatal("旁系 acquire 同模块应冲突")
		}
	})

	t.Run("descendant_acquires_ancestors_held_ok", func(t *testing.T) {
		// 任意后代 acquire 祖先已占的:跟父子同理,ancestors 含祖先 -> 成功.
		// 祖父 rid=1 占 body,孙 rid=3 acquire body(ancestors={1,2}).
		tr := tree4()
		_ = tr.TryAcquire(1, []string{"body"}, nil)
		err := tr.TryAcquire(3, []string{"body"}, map[int]struct{}{1: {}, 2: {}})
		if err != nil {
			t.Fatalf("孙共占祖父已占的 body 应成功: %v", err)
		}
		n := tr.modules["body"]
		if len(n.holders) != 2 {
			t.Fatalf("holders 应 [1,3], got %v", n.holders)
		}
	})
}

// treeFromRebuild 从 Serialize 的输出重建 Tree(模拟 server 侧行为),用 LoadFile 的
// 同一中间表示 modulesPayload 做 JSON round-trip(flat map).root 固定为 "root".
func treeFromRebuild(t *testing.T, tr *Tree) *Tree {
	t.Helper()
	data, err := json.Marshal(tr.Serialize())
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var p modulesPayload
	if err := json.Unmarshal(data, &p); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	return NewTree("root", p.Modules)
}

func TestSerializeRoundTrip(t *testing.T) {
	orig := tree4()
	rebuilt := treeFromRebuild(t, orig)

	// 拓扑等价:每个节点 id 都在,且数量一致.
	if len(rebuilt.modules) != len(orig.modules) {
		t.Fatalf("节点数不一致: orig=%d rebuilt=%d", len(orig.modules), len(rebuilt.modules))
	}
	for id := range rebuilt.modules {
		if _, ok := orig.modules[id]; !ok {
			t.Fatalf("重建树多出节点 %q", id)
		}
	}
	// cone 等价:body 的 cone 应一致(root,figure,body,leg).
	origCone := coneSetFromSerialize(orig.Serialize(), "body")
	rebuiltCone := coneSetFromSerialize(rebuilt.Serialize(), "body")
	if len(origCone) != len(rebuiltCone) {
		t.Fatalf("body cone 大小不一致: orig=%v rebuilt=%v", origCone, rebuiltCone)
	}
	for k := range origCone {
		if !rebuiltCone[k] {
			t.Fatalf("重建树 body cone 缺 %q (orig=%v rebuilt=%v)", k, origCone, rebuiltCone)
		}
	}
	wantCone := map[string]bool{"root": true, "figure": true, "body": true, "leg": true}
	if len(origCone) != len(wantCone) {
		t.Fatalf("body cone 应 4 个, got %v", origCone)
	}
	for k := range wantCone {
		if !origCone[k] {
			t.Fatalf("body cone 缺 %q", k)
		}
	}
}

func TestSerializeJSON(t *testing.T) {
	tr := tree4()
	ser := tr.Serialize()
	if _, err := json.Marshal(ser); err != nil {
		t.Fatalf("Serialize 输出无法被 json.Marshal 序列化: %v", err)
	}
}

// coneSetFromSerialize 从 Serialize 的 flat 输出算 module 的 cone(祖先+自己+后代).
// 跟 (*Tree).cone() 同语义--用 Serialize 的输出算一遍,验证序列化没丢拓扑.
func coneSetFromSerialize(ser map[string]any, m string) map[string]bool {
	out := map[string]bool{}
	mods, _ := ser["modules"].(map[string]any)
	if mods == nil {
		return out
	}
	// childrenOf 从 rec 中取 children 列表(兼容 []string 和 []any,因为 Serialize 直
	// 接输出 []string,经过 JSON round-trip 后会变成 []any).
	childrenOf := func(rec map[string]any) []string {
		if raw, ok := rec["children"].([]string); ok {
			return raw
		}
		if raw, ok := rec["children"].([]any); ok {
			out := make([]string, 0, len(raw))
			for _, c := range raw {
				if s, ok := c.(string); ok {
					out = append(out, s)
				}
			}
			return out
		}
		return nil
	}
	// 祖先:扫所有 modules,找 children 包含 cur 的就是父
	cur := m
	for cur != "" {
		out[cur] = true
		parent := ""
		for pid, rec := range mods {
			rm, ok := rec.(map[string]any)
			if !ok {
				continue
			}
			for _, c := range childrenOf(rm) {
				if c == cur {
					parent = pid
					break
				}
			}
			if parent != "" {
				break
			}
		}
		cur = parent
	}
	// 后代(沿 children 下扫)
	var walk func(id string)
	walk = func(id string) {
		rec, ok := mods[id].(map[string]any)
		if !ok {
			return
		}
		for _, c := range childrenOf(rec) {
			out[c] = true
			walk(c)
		}
	}
	walk(m)
	return out
}

func TestLoadUnloadModule(t *testing.T) {
	t.Run("load_hangs_under_parent", func(t *testing.T) {
		tr := tree4()
		if err := tr.LoadModule("figure", "newmod", ""); err != nil {
			t.Fatalf("LoadModule: %v", err)
		}
		m, ok := tr.modules["newmod"]
		if !ok || m.ParentID != "figure" {
			t.Fatalf("newmod 应挂在 figure 下, got %+v", m)
		}
		if m.Name != "newmod" {
			t.Fatalf("name 缺省应=ID, got %q", m.Name)
		}
		if !containsString(tr.modules["figure"].Children, "newmod") {
			t.Fatal("figure.Children 应含 newmod")
		}
		// cone(figure) 现在含 newmod
		has := false
		for _, c := range tr.cone("figure") {
			if c == "newmod" {
				has = true
				break
			}
		}
		if !has {
			t.Fatal("cone(figure) 应含 newmod")
		}
	})

	t.Run("load_with_name", func(t *testing.T) {
		// name 可重复(左右手都有"大拇指"),id 唯一.name != id 时 Serialize 输出 name.
		tr := tree4()
		if err := tr.LoadModule("root", "left_thumb", "大拇指"); err != nil {
			t.Fatalf("LoadModule: %v", err)
		}
		if err := tr.LoadModule("root", "right_thumb", "大拇指"); err != nil {
			t.Fatalf("LoadModule: %v", err)
		}
		l, _ := tr.modules["left_thumb"]
		r, _ := tr.modules["right_thumb"]
		if l.Name != "大拇指" || r.Name != "大拇指" {
			t.Fatalf("两者 name 都应=大拇指, got %q/%q", l.Name, r.Name)
		}
		// Serialize 输出 name(!= id 时)
		ser := tr.Serialize()
		mods, _ := ser["modules"].(map[string]any)
		rec, _ := mods["left_thumb"].(map[string]any)
		if rec["name"] != "大拇指" {
			t.Fatalf("Serialize 应输出 name=大拇指, got %v", rec)
		}
		// id 缺省 name 的节点(figure)不输出 name 字段
		figRec, _ := mods["figure"].(map[string]any)
		if _, has := figRec["name"]; has {
			t.Fatalf("name==id 不应输出 name 字段, got %v", figRec)
		}
	})

	t.Run("unload_removes_node", func(t *testing.T) {
		tr := tree4()
		_ = tr.LoadModule("figure", "newmod", "")
		if err := tr.UnloadModule("newmod"); err != nil {
			t.Fatalf("UnloadModule: %v", err)
		}
		if _, ok := tr.modules["newmod"]; ok {
			t.Fatal("unload 后 newmod 应不在")
		}
		if containsString(tr.modules["figure"].Children, "newmod") {
			t.Fatal("unload 后 figure.Children 不应含 newmod")
		}
	})

	t.Run("load_duplicate_error", func(t *testing.T) {
		tr := tree4()
		if err := tr.LoadModule("root", "body", ""); err == nil {
			t.Fatal("load 已存在的 body 应报错")
		}
	})

	t.Run("load_missing_parent_error", func(t *testing.T) {
		tr := tree4()
		if err := tr.LoadModule("nonexistent", "x", ""); err == nil {
			t.Fatal("load 到不存在的 parent 应报错")
		}
	})

	t.Run("unload_with_children_error", func(t *testing.T) {
		tr := tree4()
		// figure 有子 head/body -> 不能直接 unload
		if err := tr.UnloadModule("figure"); err == nil {
			t.Fatal("unload 有子的 figure 应报错")
		}
	})

	t.Run("unload_occupied_error", func(t *testing.T) {
		tr := tree4()
		_ = tr.TryAcquire(1, []string{"body"}, nil)
		if err := tr.UnloadModule("body"); err == nil {
			t.Fatal("unload 被占的 body 应报错")
		}
	})

	t.Run("load_then_serialize_includes_new", func(t *testing.T) {
		tr := tree4()
		_ = tr.LoadModule("core", "extra", "")
		ser := tr.Serialize()
		mods, _ := ser["modules"].(map[string]any)
		coreRec, _ := mods["core"].(map[string]any)
		// children 可能是 []string(Serialize 直接输出)或 []any(JSON round-trip 后)
		found := false
		if ch, ok := coreRec["children"].([]string); ok {
			for _, c := range ch {
				if c == "extra" {
					found = true
					break
				}
			}
		} else if ch, ok := coreRec["children"].([]any); ok {
			for _, c := range ch {
				if c == "extra" {
					found = true
					break
				}
			}
		}
		if !found {
			t.Fatalf("Serialize core.children 应含 extra, got %v", coreRec)
		}
	})
}

func containsString(s []string, v string) bool {
	for _, x := range s {
		if x == v {
			return true
		}
	}
	return false
}

func TestRenameModule(t *testing.T) {
	tr := tree4()
	// 重命名 body -> "躯干"
	if err := tr.RenameModule("body", "躯干"); err != nil {
		t.Fatalf("RenameModule: %v", err)
	}
	if tr.modules["body"].Name != "躯干" {
		t.Fatalf("body.Name 应=躯干, got %q", tr.modules["body"].Name)
	}
	// ID/ParentID/Children 不变
	if tr.modules["body"].ParentID != "figure" {
		t.Fatalf("body.ParentID 应仍=figure, got %q", tr.modules["body"].ParentID)
	}
	if !containsString(tr.modules["figure"].Children, "body") {
		t.Fatal("figure.Children 应仍含 body(ID 不变)")
	}
	// cone 不变(重命名不影响拓扑)
	cone := tr.cone("body")
	coneSet := map[string]bool{}
	for _, c := range cone {
		coneSet[c] = true
	}
	want := map[string]bool{"root": true, "figure": true, "body": true, "leg": true}
	if len(coneSet) != len(want) {
		t.Fatalf("rename 后 body cone 大小应 4, got %v", coneSet)
	}
	for k := range want {
		if !coneSet[k] {
			t.Fatalf("body cone 缺 %q", k)
		}
	}

	// 不存在的模块报错
	if err := tr.RenameModule("nonexistent", "x"); err == nil {
		t.Fatal("rename 不存在的模块应报错")
	}
	// 空 name 回退到 ID
	tr.RenameModule("leg", "")
	if tr.modules["leg"].Name != "leg" {
		t.Fatalf("空 name 应回退为 ID(leg), got %q", tr.modules["leg"].Name)
	}

	// 序列化后 name 字段更新
	ser := tr.Serialize()
	mods, _ := ser["modules"].(map[string]any)
	bodyRec, _ := mods["body"].(map[string]any)
	if bodyRec["name"] != "躯干" {
		t.Fatalf("Serialize 应输出 name=躯干, got %v", bodyRec)
	}
}

func TestMoveModule(t *testing.T) {
	t.Run("move_basic", func(t *testing.T) {
		tr := tree4()
		// 把 mouth 从 core 下移到 root 下
		if err := tr.MoveModule("mouth", "root"); err != nil {
			t.Fatalf("MoveModule: %v", err)
		}
		m := tr.modules["mouth"]
		if m.ParentID != "root" {
			t.Fatalf("mouth.ParentID 应=root, got %q", m.ParentID)
		}
		// core 不再有 mouth
		if containsString(tr.modules["core"].Children, "mouth") {
			t.Fatal("core.Children 不应含 mouth")
		}
		// root 有 mouth
		if !containsString(tr.modules["root"].Children, "mouth") {
			t.Fatal("root.Children 应含 mouth")
		}
		// cone(mouth) 祖先变了:{root, mouth}(不再含 core)
		cone := tr.cone("mouth")
		set := map[string]bool{}
		for _, c := range cone {
			set[c] = true
		}
		if set["core"] {
			t.Fatalf("move 后 mouth cone 不应含 core, got %v", set)
		}
		if !set["root"] || !set["mouth"] {
			t.Fatalf("mouth cone 应含 root+mouth, got %v", set)
		}
	})

	t.Run("move_leaf_to_descendant_of_itself_cycle_error", func(t *testing.T) {
		tr := tree4()
		// 试图把 figure 移到 body 下(figure 是 body 的祖先,移动后 body→figure→body 形成环)
		if err := tr.MoveModule("figure", "body"); err == nil {
			t.Fatal("figure 移到后代 body 下应报错(环)")
		}
	})

	t.Run("move_to_self_error", func(t *testing.T) {
		tr := tree4()
		if err := tr.MoveModule("body", "body"); err == nil {
			t.Fatal("把 body 移到自己下应报错")
		}
	})

	t.Run("move_nonexistent_error", func(t *testing.T) {
		tr := tree4()
		if err := tr.MoveModule("nonexistent", "root"); err == nil {
			t.Fatal("move 不存在模块应报错")
		}
		if err := tr.MoveModule("body", "nonexistent"); err == nil {
			t.Fatal("move 到不存在父应报错")
		}
	})

	t.Run("move_to_same_parent_noop", func(t *testing.T) {
		tr := tree4()
		// body 当前在 figure 下,再移到 figure 下是 no-op
		before := tr.modules["body"].ParentID
		if err := tr.MoveModule("body", "figure"); err != nil {
			t.Fatalf("同父 move 应 no-op 成功: %v", err)
		}
		if tr.modules["body"].ParentID != before {
			t.Fatal("同父 move 不应改变 ParentID")
		}
	})

	t.Run("move_with_holders_allowed", func(t *testing.T) {
		// 被占用时允许移动(语义:移动目录不释放占用)
		tr := tree4()
		_ = tr.TryAcquire(1, []string{"mouth"}, nil)
		if err := tr.MoveModule("mouth", "root"); err != nil {
			t.Fatalf("有 holder 时 move 应允许: %v", err)
		}
		// holders 不变
		if !tr.modules["mouth"].hasHolder(1) {
			t.Fatal("move 后 holder 应保留")
		}
	})

	t.Run("move_preserves_subtree", func(t *testing.T) {
		// 移动 body(有子 leg)到 core 下,body 子树(body→leg)整体跟随
		tr := tree4()
		if err := tr.MoveModule("body", "core"); err != nil {
			t.Fatalf("MoveModule: %v", err)
		}
		if tr.modules["body"].ParentID != "core" {
			t.Fatalf("body.ParentID 应=core, got %q", tr.modules["body"].ParentID)
		}
		if tr.modules["leg"].ParentID != "body" {
			t.Fatalf("leg.ParentID 应仍=body(子树跟随), got %q", tr.modules["leg"].ParentID)
		}
		// core cone 现在应含 body+leg
		coreCone := tr.cone("core")
		set := map[string]bool{}
		for _, c := range coreCone {
			set[c] = true
		}
		if !set["body"] || !set["leg"] {
			t.Fatalf("core cone 应含 body+leg, got %v", set)
		}
	})
}
