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
// JSON 解码把 string 列表原样保留,但保险起见兼容 float64.
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
