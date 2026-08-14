package conn

import "strconv"

// ToInt 把 wire 上的 id(string 或 float64)转 int.
func ToInt(v any) int {
	switch x := v.(type) {
	case string:
		n, err := strconv.Atoi(x)
		if err != nil {
			return 0
		}
		return n
	case float64:
		return int(x)
	}
	return 0
}

// ToStringSlice 把 wire 上的 modules([]any of string/float64)转 []string.
// MessageToDict 把 string 列表原样保留,但保险起见兼容 float64.
func ToStringSlice(v any) []string {
	arr, _ := v.([]any)
	out := make([]string, 0, len(arr))
	for _, m := range arr {
		switch x := m.(type) {
		case string:
			out = append(out, x)
		case float64:
			out = append(out, strconv.Itoa(int(x)))
		}
	}
	return out
}

// ToAnySlice 把 []string 转 []any----structpb.NewStruct 序列化 []any of string 时
// 要求 []any,[]string 会报 "proto: invalid type: []string".发 modules 等字符串列表
// 到 wire 时过一道.
func ToAnySlice(s []string) []any {
	out := make([]any, len(s))
	for i, v := range s {
		out[i] = v
	}
	return out
}
