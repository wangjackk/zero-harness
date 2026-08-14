package command

import (
	"context"
	"testing"
)

func TestAddRemoveModules(t *testing.T) {
	c := New("x", func(_ context.Context, _ *Command) {})
	c.SetModules("body")

	// acquire leg + mouth → 并集进 Modules(去重)
	c.AddModules("leg", "mouth", "body") // body 已有,去重
	got := map[string]bool{}
	for _, m := range c.Modules {
		got[m] = true
	}
	for _, want := range []string{"body", "leg", "mouth"} {
		if !got[want] {
			t.Errorf("AddModules 后 Modules 缺 %s, got=%v", want, c.Modules)
		}
	}
	if len(c.Modules) != 3 {
		t.Errorf("Modules 应去重为 3 个, got=%v", c.Modules)
	}

	// release leg → 移除
	c.RemoveModules("leg")
	got = map[string]bool{}
	for _, m := range c.Modules {
		got[m] = true
	}
	if got["leg"] {
		t.Errorf("RemoveModules 后 leg 应移除, got=%v", c.Modules)
	}
	if !got["body"] || !got["mouth"] {
		t.Errorf("body/mouth 应保留, got=%v", c.Modules)
	}
}
