package grpc

import (
	"context"
	"fmt"

	"google.golang.org/protobuf/types/known/structpb"
)

// Req 查询(get_*),返回回报 dict.
func (c *Client) Req(ctx context.Context, msg map[string]any) (map[string]any, error) {
	s, err := structpb.NewStruct(msg)
	if err != nil {
		return nil, fmt.Errorf("marshal: %w", err)
	}
	resp, err := c.service.Req(ctx, s)
	if err != nil {
		return nil, err
	}
	return resp.AsMap(), nil
}
