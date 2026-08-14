package module

import (
	"encoding/json"
	"fmt"
	"os"
)

// tree.json 格式(flat map keyed by module_id):
//
//	{
//	  "modules": {
//	    "root":   {"children": ["figure", "core"]},
//	    "figure": {"children": ["head", "body"]},
//	    "head":   {},
//	    "body":   {"children": ["leg"]},
//	    "leg":    {},
//	    "core":   {"children": ["mouth"]},
//	    "mouth":  {}
//	  }
//	}
//
// root = 固定 id "root"(不依赖 parent_id 反推).
type modulesPayload struct {
	Modules map[string]ModuleRecord `json:"modules"`
}

// LoadFile 从 JSON 文件加载模块树,返回 *Tree.flat map 格式(见上).
func LoadFile(path string) (*Tree, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	var p modulesPayload
	if err := json.Unmarshal(data, &p); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	if len(p.Modules) == 0 {
		return nil, fmt.Errorf("%s: no modules", path)
	}
	const rootID = "root"
	if _, ok := p.Modules[rootID]; !ok {
		return nil, fmt.Errorf("%s: module %q (root) not found", path, rootID)
	}
	return NewTree(rootID, p.Modules), nil
}
