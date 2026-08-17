package grpc

import (
	"context"
	"fmt"

	"github.com/bytedance/sonic"
)

// mapToFrame 边界编码:map → Frame(payload=紧凑 JSON 文本).sonic JIT,无 HTML
// 转义/无 key 排序(两端按 JSON 解析,不依赖字节形态).
func mapToFrame(m map[string]any) (*Frame, error) {
	b, err := sonic.Marshal(m)
	if err != nil {
		return nil, err
	}
	return &Frame{Payload: string(b)}, nil
}

// frameToMap 边界解码:Frame → map.格式不对直接报错暴露协议破坏,不做兼容读.
func frameToMap(f *Frame) (map[string]any, error) {
	var m map[string]any
	if err := sonic.Unmarshal([]byte(f.Payload), &m); err != nil {
		return nil, err
	}
	return m, nil
}

// Req 查询(get_*),返回回报 dict.
func (c *Client) Req(ctx context.Context, msg map[string]any) (map[string]any, error) {
	f, err := mapToFrame(msg)
	if err != nil {
		return nil, fmt.Errorf("marshal: %w", err)
	}
	resp, err := c.service.Req(ctx, f)
	if err != nil {
		return nil, err
	}
	return frameToMap(resp)
}
