package routine

import "sync"

// ModuleTree 模块树拓扑缓存,业务侧编排策略算 cone/conflict 用.
// 对标 Python routine/module_tree.py.
//
// kernel 推 module.tree 事件后缓存;cone/conflict 纯本地计算,零 round-trip.
type ModuleTree struct {
	rootID  string
	parents map[string]string // module → parent(root 的 parent 为 "")
	children map[string][]string
	names   map[string]string
	coneCache map[string]map[string]bool
	mu      sync.RWMutex
}

// NewModuleTreeFromDict 从 kernel 推来的 flat 拓扑构造.
// treePayload = {"root": rootID, "modules": {id: {"children": [...], "name": "..."}}}
func NewModuleTreeFromDict(treePayload map[string]any) (*ModuleTree, error) {
	modules, ok := treePayload["modules"].(map[string]any)
	if !ok {
		return nil, errString("invalid module tree payload: missing modules")
	}

	parents := make(map[string]string)
	children := make(map[string][]string)
	names := make(map[string]string)

	for mid, rec := range modules {
		nm := mid
		var ch []string
		if recMap, ok := rec.(map[string]any); ok {
			if n, ok := recMap["name"].(string); ok && n != "" {
				nm = n
			}
			if chList, ok := recMap["children"].([]any); ok {
				for _, c := range chList {
					if cs, ok := c.(string); ok {
						ch = append(ch, cs)
					}
				}
			}
		}
		parents[mid] = "" // 占位,下面根据 children 反推
		names[mid] = nm
		children[mid] = ch
	}
	// 根据 children 反推 parent
	for pid, chlist := range children {
		for _, c := range chlist {
			parents[c] = pid
		}
	}

	rootID, _ := treePayload["root"].(string)
	if rootID == "" {
		rootID = "root"
	}
	if _, ok := parents[rootID]; !ok {
		return nil, errString("root not in module tree payload")
	}

	return &ModuleTree{
		rootID:    rootID,
		parents:   parents,
		children:  children,
		names:     names,
		coneCache: make(map[string]map[string]bool),
	}, nil
}

// Cone 返回 module 的冲突锥:祖先 + 自己 + 后代.
// 跟 kernel (*Node).cone() 同语义.module 不在树里返回空集.
func (t *ModuleTree) Cone(module string) map[string]bool {
	t.mu.RLock()
	if cached, ok := t.coneCache[module]; ok {
		t.mu.RUnlock()
		return cached
	}
	t.mu.RUnlock()

	if _, ok := t.parents[module]; !ok {
		return nil
	}

	out := make(map[string]bool)
	// 祖先
	p := t.parents[module]
	for p != "" {
		out[p] = true
		p = t.parents[p]
	}
	// 自己
	out[module] = true
	// 后代(DFS)
	stack := append([]string{}, t.children[module]...)
	for len(stack) > 0 {
		c := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		out[c] = true
		stack = append(stack, t.children[c]...)
	}

	t.mu.Lock()
	t.coneCache[module] = out
	t.mu.Unlock()
	return out
}

// Conflict 两组 modules 是否冲突:任一对的 cone 相交即冲突.
// 空集(任一为空)视为无冲突.未知模块名视为无冲突(保守放行).
func (t *ModuleTree) Conflict(modsA, modsB []string) bool {
	if len(modsA) == 0 || len(modsB) == 0 {
		return false
	}
	aCone := make(map[string]bool)
	for _, m := range modsA {
		for k := range t.Cone(m) {
			aCone[k] = true
		}
	}
	for _, m := range modsB {
		if aCone[m] {
			return true
		}
	}
	return false
}

// NameOf 模块的显示名(缺省 = id).
func (t *ModuleTree) NameOf(moduleID string) string {
	if n, ok := t.names[moduleID]; ok {
		return n
	}
	return moduleID
}

// RootID 返回根模块 id.
func (t *ModuleTree) RootID() string { return t.rootID }

func errString(msg string) error {
	return &simpleError{msg: msg}
}

type simpleError struct{ msg string }

func (e *simpleError) Error() string { return e.msg }
