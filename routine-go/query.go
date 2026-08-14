package routine

import (
	"fmt"

	"google.golang.org/protobuf/types/known/structpb"
)

// QueryService Req unary 查询:get_modules / get_routines,以及 module.tree 推送缓存.
// 对标 Python routine/query.py.
type QueryService struct {
	hub     *RoutineHub
	runtime *ServerRuntime
}

func NewQueryService(hub *RoutineHub, runtime *ServerRuntime) *QueryService {
	return &QueryService{hub: hub, runtime: runtime}
}

// HandleReq 处理 dial-out 下的 kernel→routine Req 查询.
// 返回 Struct reply.
func (q *QueryService) HandleReq(request *structpb.Struct) *structpb.Struct {
	msg := structToMap(request)
	event, _ := msg["event"].(string)

	switch event {
	case ModuleTreeEvent:
		ok := q.CacheModuleTree(msg)
		return mapToStruct(map[string]any{"ok": ok})

	case ReqGetModules:
		return mapToStruct(map[string]any{"modules": q.runtime.Modules})

	case ReqGetRoutines:
		// dial-out:hub_id 随首次 get_routines 响应带给 kernel.
		return mapToStruct(map[string]any{
			"routines": q.BuildRoutines(),
			"hub_id":   q.hub.HubID,
		})
	}

	return mapToStruct(map[string]any{})
}

// CacheModuleTree 缓存 kernel 推来的 module.tree 拓扑(Req/Stream 共用).成功 true.
func (q *QueryService) CacheModuleTree(msg map[string]any) bool {
	treePayload, ok := msg["tree"].(map[string]any)
	if !ok {
		q.hub.logger.Warnf("🌳 module.tree payload 缺 tree: %v", msg)
		return false
	}
	tree, err := NewModuleTreeFromDict(treePayload)
	if err != nil {
		q.hub.logger.Warnf("🌳 module.tree 解析失败: %v (payload=%v)", err, treePayload)
		return false
	}
	q.runtime.SetModuleTree(tree)
	q.hub.logger.Infof("🌳 module tree cached: %s", tree.RootID())
	return true
}

// BuildRoutines 组装 routine 列表(get_routines Req 与 catalog.push push 共用).
func (q *QueryService) BuildRoutines() []map[string]any {
	factories := q.runtime.Registry.All()
	out := make([]map[string]any, 0, len(factories))
	for _, f := range factories {
		meta := map[string]any{}
		for k, v := range f.Meta() {
			meta[k] = v
		}
		out = append(out, map[string]any{
			"name":        f.Name(),
			"is_passive":  f.IsPassive(),
			"meta":        meta,
		})
	}
	return out
}

// --- struct ↔ map 转换 helpers ---

// structToMap 把 structpb.Struct 转成 map[string]any.
func structToMap(s *structpb.Struct) map[string]any {
	return structAsMap(s)
}

func structAsMap(s *structpb.Struct) map[string]any {
	if s == nil {
		return map[string]any{}
	}
	out := make(map[string]any, len(s.Fields))
	for k, v := range s.Fields {
		out[k] = valueAsAny(v)
	}
	return out
}

func valueAsAny(v *structpb.Value) any {
	if v == nil {
		return nil
	}
	switch vv := v.Kind.(type) {
	case *structpb.Value_NullValue:
		return nil
	case *structpb.Value_NumberValue:
		return vv.NumberValue
	case *structpb.Value_StringValue:
		return vv.StringValue
	case *structpb.Value_BoolValue:
		return vv.BoolValue
	case *structpb.Value_StructValue:
		return structAsMap(vv.StructValue)
	case *structpb.Value_ListValue:
		out := make([]any, 0, len(vv.ListValue.Values))
		for _, item := range vv.ListValue.Values {
			out = append(out, valueAsAny(item))
		}
		return out
	}
	return nil
}

func mapToStruct(m map[string]any) *structpb.Struct {
	s, err := structpb.NewStruct(mapToJSONMap(m))
	if err != nil {
		return &structpb.Struct{}
	}
	return s
}

func mapToJSONMap(m map[string]any) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = toJSONValue(v)
	}
	return out
}

func toJSONValue(v any) any {
	switch vv := v.(type) {
	case map[string]any:
		return mapToJSONMap(vv)
	case []map[string]any:
		out := make([]any, 0, len(vv))
		for _, item := range vv {
			out = append(out, toJSONValue(item))
		}
		return out
	case []any:
		out := make([]any, 0, len(vv))
		for _, item := range vv {
			out = append(out, toJSONValue(item))
		}
		return out
	case []string:
		out := make([]any, 0, len(vv))
		for _, s := range vv {
			out = append(out, s)
		}
		return out
	case []int:
		out := make([]any, 0, len(vv))
		for _, n := range vv {
			out = append(out, n)
		}
		return out
	case nil:
		return nil
	default:
		// number / string / bool 直接使用(structpb.NewStruct 支持)
		// 其他类型(fmt.Stringer 等)转 string
		if _, ok := vv.(string); ok {
			return vv
		}
		if _, ok := vv.(float64); ok {
			return vv
		}
		if _, ok := vv.(int); ok {
			return vv
		}
		if _, ok := vv.(int64); ok {
			return vv
		}
		if _, ok := vv.(bool); ok {
			return vv
		}
		return fmt.Sprint(vv)
	}
}
