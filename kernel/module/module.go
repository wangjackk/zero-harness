package module

import (
	"fmt"
	"strings"
	"sync"
)

// Module 模块节点.存储为 flat map(module_id 为 key),ParentID 由 children 反推
// 填充(缓存便于 cone 祖先上溯).holders 队列:被占时记录所有占用的 routine id(父子叠加).
type Module struct {
	ID       string
	Name     string   // 显示名,可重复(如左右手都有"大拇指");缺省=ID
	ParentID string   // root 为 ""
	Children []string // 直接子 module_id 列表
	holders  []int
}

func (m *Module) hasHolder(rid int) bool {
	for _, h := range m.holders {
		if h == rid {
			return true
		}
	}
	return false
}

func (m *Module) removeHolder(rid int) {
	out := m.holders[:0]
	for _, h := range m.holders {
		if h != rid {
			out = append(out, h)
		}
	}
	m.holders = out
}

// ModuleRecord 模块记录的磁盘/wire 格式(flat map 的 value).Name 可重复(渲染用),
// 缺省=ID;Children 为直接子 module_id 列表(叶子节点省略),root 由固定 id "root" 标识.
type ModuleRecord struct {
	Name     string   `json:"name,omitempty"`
	Children []string `json:"children,omitempty"`
}

// Tree 模块树.全局唯一(见 Default/Init).存储为 module_id 为 key 的 flat map,
// 便于动态 Load/Unload 子模块(map 增删 + Children 列表,不动嵌套指针结构).
// ParentID 由 Children 反推填充(缓存便于 cone 祖先上溯).
type Tree struct {
	mu      sync.Mutex
	root    string
	modules map[string]*Module
}

// NewTree 从 flat records 构造(rootID + records).ParentID 由 Children 反推填充.
// 对标 LoadFile 的同款输入;测试也用此构造.
func NewTree(rootID string, records map[string]ModuleRecord) *Tree {
	t := &Tree{root: rootID, modules: map[string]*Module{}}
	for id, rec := range records {
		name := rec.Name
		if name == "" {
			name = id // 缺省 name=ID
		}
		t.modules[id] = &Module{ID: id, Name: name, Children: rec.Children}
	}
	for _, m := range t.modules {
		for _, c := range m.Children {
			if child, ok := t.modules[c]; ok {
				child.ParentID = m.ID
			}
		}
	}
	return t
}

// RootID 返回根模块 id(root routine 占根模块用).
func (t *Tree) RootID() string {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.root
}

// ConflictError 模块被别的 routine 占用导致 Start 被拒.
type ConflictError struct {
	Requested string // 请求的模块
	BlockedBy string // 实际被占,造成冲突的节点
	Holders   []int  // 该节点当前的占用者 routine id 队列
}

func (e *ConflictError) Error() string {
	return fmt.Sprintf("module %q blocked: %q held by routines %v", e.Requested, e.BlockedBy, e.Holders)
}

// cone 返回 module 的冲突锥:祖先 + 自己 + 后代(module_id 集).module 不存在返 nil.
// 占用 m 会挡住 cone 内任意节点;cone 内任意节点被占也会挡住 m.
func (t *Tree) cone(m string) []string {
	n, ok := t.modules[m]
	if !ok {
		return nil
	}
	out := map[string]struct{}{}
	// 祖先(沿 ParentID 上溯)
	for pid := n.ParentID; pid != ""; {
		out[pid] = struct{}{}
		if pn, ok := t.modules[pid]; ok {
			pid = pn.ParentID
		} else {
			break
		}
	}
	// 自己
	out[m] = struct{}{}
	// 后代(沿 Children 下扫)
	var walk func(id string)
	walk = func(id string) {
		if cn, ok := t.modules[id]; ok {
			for _, c := range cn.Children {
				out[c] = struct{}{}
				walk(c)
			}
		}
	}
	walk(m)
	result := make([]string, 0, len(out))
	for id := range out {
		result = append(result, id)
	}
	return result
}

// TryAcquire 为 routine rid 占用 modules.check-and-occupy 在同一锁临界区内,原子.
// 成功返回 nil;失败返回 *ConflictError(被占)或描述缺失模块的 error.
// ancestors 是 rid 在 routine 树上的祖先 command id 集合:cone 内被占节点的 holder 若全是
// rid 自己或其祖先,放行(父子协作共占同一模块节点);否则冲突(外人抢占).
// 只在声明的模块上打 tag,cone 内其余节点不动--靠 cone 查来挡.
func (t *Tree) TryAcquire(rid int, modules []string, ancestors map[int]struct{}) error {
	t.mu.Lock()
	defer t.mu.Unlock()

	// 第一遍:check cone.被占节点的 holder 只要有一个既不是 rid 自己,也不在 ancestors,就冲突.
	for _, m := range modules {
		if _, ok := t.modules[m]; !ok {
			return fmt.Errorf("module %q not found in tree", m)
		}
		for _, c := range t.cone(m) {
			cn := t.modules[c]
			for _, h := range cn.holders {
				if h == rid {
					continue
				}
				if _, ok := ancestors[h]; ok {
					continue // 父祖先占用,允许共占
				}
				return &ConflictError{Requested: m, BlockedBy: c, Holders: append([]int(nil), cn.holders...)}
			}
		}
	}
	// 第二遍:占用.声明节点 append rid(去重,同 rid 多次声明同模块只占一次).
	for _, m := range modules {
		n := t.modules[m]
		if !n.hasHolder(rid) {
			n.holders = append(n.holders, rid)
		}
	}
	return nil
}

// EvictableHolders 返回占住 modules 的 cone 内,需被 force 驱逐的第三方 holder rid 集合.
// 排除 rid 自己,ancestors 里的(父祖先共占放行).ancestors 包含 root(root 是所有
// routine 的顶层祖先),故 root 永远不会出现在结果里--满足"force 永不动 root".
// 跟 TryAcquire 检查的 cone 完全一致:驱逐这批人之后(无竞态时)TryAcquire 必过.
// force_release / force_start 用:先拿这批 holder,cascade stop,再 TryAcquire.
//
// 不存在的 module 静默跳过(force 时由调用方另行校验--本方法只管"谁挡我").
func (t *Tree) EvictableHolders(modules []string, ancestors map[int]struct{}, rid int) []int {
	t.mu.Lock()
	defer t.mu.Unlock()
	set := map[int]struct{}{}
	for _, m := range modules {
		if _, ok := t.modules[m]; !ok {
			continue
		}
		for _, c := range t.cone(m) {
			cn := t.modules[c]
			for _, h := range cn.holders {
				if h == rid {
					continue
				}
				if _, ok := ancestors[h]; ok {
					continue // 父祖先共占,放行
				}
				set[h] = struct{}{}
			}
		}
	}
	out := make([]int, 0, len(set))
	for h := range set {
		out = append(out, h)
	}
	return out
}

// Release 释放 routine rid 占用的所有模块(从 holders 队列移除 rid).
// 用于 routine 停止时全量清理(runRemote defer 调).
func (t *Tree) Release(rid int) {
	t.mu.Lock()
	defer t.mu.Unlock()
	for _, n := range t.modules {
		if n.hasHolder(rid) {
			n.removeHolder(rid)
		}
	}
}

// ReleaseModules 释放 routine rid 占用的指定模块(从这些节点的 holders 移除 rid).
// 用于运行时 routine.release(modules)--只释放指定模块,不全量.
// 节点不存在或 rid 未占该节点都是 no-op.
func (t *Tree) ReleaseModules(rid int, modules []string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	for _, m := range modules {
		if n, ok := t.modules[m]; ok && n.hasHolder(rid) {
			n.removeHolder(rid)
		}
	}
}

// LoadModule 往 parentID 下挂 childID(全局树动态增子模块).name 是显示名(可重复,
// 如左右手都有"大拇指"),空则用 childID.只挂树不占用--占用另调 TryAcquire/acquire.
// child 已存在 / parent 不存在报错.
func (t *Tree) LoadModule(parentID, childID, name string) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if childID == "" {
		return fmt.Errorf("child_id is empty")
	}
	if _, ok := t.modules[childID]; ok {
		return fmt.Errorf("module %q already exists", childID)
	}
	p, ok := t.modules[parentID]
	if !ok {
		return fmt.Errorf("parent module %q not found", parentID)
	}
	if name == "" {
		name = childID
	}
	t.modules[childID] = &Module{ID: childID, Name: name, ParentID: parentID}
	p.Children = append(p.Children, childID)
	return nil
}

// UnloadModule 摘掉 childID(全局树动态删子模块).有子模块 / 有 holders 报错
// (必须先 unload 子孙,先 release 占用).child 不存在报错.
func (t *Tree) UnloadModule(childID string) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	n, ok := t.modules[childID]
	if !ok {
		return fmt.Errorf("module %q not found", childID)
	}
	if len(n.Children) > 0 {
		return fmt.Errorf("module %q has children %v, unload them first", childID, n.Children)
	}
	if len(n.holders) > 0 {
		return fmt.Errorf("module %q is occupied by routines %v, release first", childID, n.holders)
	}
	if n.ParentID != "" {
		if p, ok := t.modules[n.ParentID]; ok {
			p.Children = removeString(p.Children, childID)
		}
	}
	delete(t.modules, childID)
	return nil
}

func removeString(s []string, v string) []string {
	out := s[:0]
	for _, x := range s {
		if x != v {
			out = append(out, x)
		}
	}
	return out
}

// RenameModule 修改模块显示名(对应文件夹重命名).ID/ParentID/Children/holders 均
// 不变,只改 Name.模块不存在报错.name 为空则用 ID(跟 LoadModule 一致).
// 成功后调用方负责重推 module.tree 给所有 conn.
func (t *Tree) RenameModule(id, newName string) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	n, ok := t.modules[id]
	if !ok {
		return fmt.Errorf("module %q not found", id)
	}
	if newName == "" {
		newName = id
	}
	n.Name = newName
	return nil
}

// MoveModule 把模块从旧父下移动到 newParentID 下(对应文件夹跨目录移动).ID/Name/
// Children/holders 均不变,只改 ParentID 和父子 Children 列表.
// 模块不存在 / 新父不存在 / 新父是自己后代(会形成环) 报错.
// 注:holders 不检查——routine 占用模块时允许移动(移动目录不释放占用,语义合理).
// 成功后调用方负责重推 module.tree 给所有 conn.
func (t *Tree) MoveModule(id, newParentID string) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	n, ok := t.modules[id]
	if !ok {
		return fmt.Errorf("module %q not found", id)
	}
	if newParentID == "" {
		return fmt.Errorf("new_parent_id is empty")
	}
	np, ok := t.modules[newParentID]
	if !ok {
		return fmt.Errorf("new parent module %q not found", newParentID)
	}
	if newParentID == id {
		return fmt.Errorf("cannot move module %q under itself", id)
	}
	// 环检测:newParentID 不能是 id 的后代(含自己,上面已查).
	if t.isDescendantLocked(id, newParentID) {
		return fmt.Errorf("cannot move %q under %q: would create cycle", id, newParentID)
	}
	// 新旧父相同是 no-op.
	if n.ParentID == newParentID {
		return nil
	}
	// 从旧父 Children 移除.
	if n.ParentID != "" {
		if op, ok := t.modules[n.ParentID]; ok {
			op.Children = removeString(op.Children, id)
		}
	}
	// 挂到新父下.
	n.ParentID = newParentID
	np.Children = append(np.Children, id)
	return nil
}

// isDescendantLocked 判断 candidate 是否是 ancestor 的后代(含 ancestor 自己).
// 调用方必须已持锁.沿 ancestor.Children 下扫.
func (t *Tree) isDescendantLocked(ancestor, candidate string) bool {
	var walk func(id string) bool
	walk = func(id string) bool {
		if id == candidate {
			return true
		}
		n, ok := t.modules[id]
		if !ok {
			return false
		}
		for _, c := range n.Children {
			if walk(c) {
				return true
			}
		}
		return false
	}
	return walk(ancestor)
}

// PrintTree 以树状结构返回模块树字符串.
// 用于启动时打印模块树,便于确认加载结果.
//
//	root
//	├── figure
//	│   ├── head
//	│   └── body
//	│       └── leg
//	└── core
//	    └── mouth
func PrintTree(t *Tree) string {
	if t == nil {
		return ""
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	var b strings.Builder
	printMod(&b, t, t.root, "", true, true)
	return b.String()
}

func printMod(b *strings.Builder, t *Tree, id, prefix string, isLast, isRoot bool) {
	m, ok := t.modules[id]
	if !ok {
		return
	}
	var connector string
	if !isRoot {
		if isLast {
			connector = "└── "
		} else {
			connector = "├── "
		}
	}
	label := m.Name
	if label == "" {
		label = id
	}
	fmt.Fprintf(b, "%s%s%s\n", prefix, connector, label)
	if !isRoot {
		if isLast {
			prefix += "    "
		} else {
			prefix += "│   "
		}
	}
	for i, c := range m.Children {
		printMod(b, t, c, prefix, i == len(m.Children)-1, false)
	}
}

// Serialize 把树序列化成 flat map(module_id 为 key),格式:
// {"root": rootID, "modules": {id: {"children": [...], "name": "..."}, ...}}.
// 叶子节点 children 省略(omitempty);name==ID 省略(缺省语义,省 wire).
// holders 是运行时状态不序列化.用于 kernel 推 module.tree 给 routine
// server--server 据此重建本地 ModuleTree 缓存,算 cone/conflict.跟 LoadFile 的 JSON
// 输入同构:Serialize 的输出能直接 JSON marshal 后被 LoadFile 读回.
func (t *Tree) Serialize() map[string]any {
	t.mu.Lock()
	defer t.mu.Unlock()
	mods := make(map[string]any, len(t.modules))
	for id, m := range t.modules {
		rec := map[string]any{}
		if m.Name != "" && m.Name != m.ID {
			rec["name"] = m.Name // name==ID 不输出(缺省语义,省 wire)
		}
		if len(m.Children) > 0 {
			// structpb.NewStruct 要求 slice 必须是 []any,不能 []string,否则报
			// "proto: invalid type: []string".这里就地转.
			kids := make([]any, len(m.Children))
			for i, c := range m.Children {
				kids[i] = c
			}
			rec["children"] = kids
		}
		mods[id] = rec
	}
	return map[string]any{
		"root":    t.root,
		"modules": mods,
	}
}

// 全局唯一树
var defaultTree *Tree

func Init(t *Tree) { defaultTree = t }
func Default() *Tree  { return defaultTree }
