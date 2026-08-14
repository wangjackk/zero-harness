package shell

import (
	"strconv"

	"kernel/conn"
)

// broker 中央化转发(对标老版 router 的 dumb forward + pubsub fanout).
// pubsub 订阅表 + message/body 转发都在 Manager----按 target/subscriber 查 nodes
// 定 clientID,publish 到该 conn 的出站 topic(conn.out.<clientID>)让 sendLoop 发
// delivered,跨 server 路由.
//
// 限制(已实现跨 server):routine 之间的 req/message/pubsub/yield 现在都能跨 server.
// 来源 routine 在 server A 发 message 给 server B 的 routine----Manager 查 target 的 clientID,
// publish 到 server B 的出站 topic 投递 delivered.

// pubsubKey 把 (namespace, topic) 组合成订阅表 key.
// 用 "\x00" 分隔避免 ("a","bc") 与 ("ab","c") 碰撞;\x00 不会出现在正常 namespace/topic 里.
func pubsubKey(namespace, topic string) string {
	return namespace + "\x00" + topic
}

// clientIDOfNode 按 routine 的 command id(int)查它所属的 conn id.routine 跨 server
// 路由的核心:target/subscriber 在哪个 server,就 publish 到那个 conn 的出站 topic.
// 找不到(id 不在 nodes,如 routine 已 stopped 或 id 非法)返回空串.
func (m *Manager) clientIDOfNode(idStr string) string {
	rid, err := strconv.Atoi(idStr)
	if err != nil {
		return ""
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	n := m.nodes[rid]
	if n == nil {
		return ""
	}
	return n.clientID
}

// OnPubsubSubscribe 把 subscriber id 加进 (namespace, topic) 的订阅集合.
// 订阅表在 Manager(中央),subscriber 跨 server 都注册到这里.
func (m *Manager) OnPubsubSubscribe(topic, namespace, subscriberID string) {
	if topic == "" || subscriberID == "" {
		return
	}
	key := pubsubKey(namespace, topic)
	m.mu.Lock()
	s, ok := m.pubsub[key]
	if !ok {
		s = map[string]struct{}{}
		m.pubsub[key] = s
	}
	s[subscriberID] = struct{}{}
	m.mu.Unlock()
}

// OnPubsubUnsubscribe 把 subscriber id 从 (namespace, topic) 的订阅集合移除.
func (m *Manager) OnPubsubUnsubscribe(topic, namespace, subscriberID string) {
	key := pubsubKey(namespace, topic)
	m.mu.Lock()
	s, ok := m.pubsub[key]
	if !ok {
		m.mu.Unlock()
		return
	}
	delete(s, subscriberID)
	if len(s) == 0 {
		delete(m.pubsub, key)
	}
	m.mu.Unlock()
}

// OnPubsubPublish fanout:对 (namespace, topic) 的每个订阅者发一条 pubsub.delivered.
// 订阅者可能在任意 server----按 subscriber_id 查 client 跨 server 投递.
// data 透传,source = 发布者 id(kernel 不查 name,python 侧自行补).namespace 也透传.
func (m *Manager) OnPubsubPublish(topic, namespace, sourceID string, data any) {
	key := pubsubKey(namespace, topic)
	m.mu.Lock()
	s, ok := m.pubsub[key]
	if !ok {
		m.mu.Unlock()
		return
	}
	// 快照订阅者(fanout 期间可能 unsubscribe,避免迭代中改 map)
	subs := make([]string, 0, len(s))
	for sid := range s {
		subs = append(subs, sid)
	}
	m.mu.Unlock()

	for _, subscriberID := range subs {
		clientID := m.clientIDOfNode(subscriberID)
		if clientID == "" {
			continue // subscriber 已 stopped / 不存在,跳过
		}
		delivered := map[string]any{
			"event":         conn.PubsubDelivered,
			"subscriber_id": subscriberID,
			"topic":         topic,
			"namespace":     namespace,
			"source":        map[string]any{"id": sourceID},
		}
		if data != nil {
			delivered["data"] = data
		}
		m.sendOut(clientID, delivered)
	}
}

// OnRoutineStopped 清掉该 id 作为 subscriber 在所有 topic 的订阅(自动退订,
// 对标老版 Command 销毁即清订阅).订阅表在 Manager.
func (m *Manager) OnRoutineStopped(id string) {
	m.mu.Lock()
	for key, s := range m.pubsub {
		delete(s, id)
		if len(s) == 0 {
			delete(m.pubsub, key)
		}
	}
	m.mu.Unlock()
}

// OnMessage 是 message.* 的通用 dumb forward:把 sender 发来的任意 message.*
// send 类事件,按 target_ids 逐个转发成对应的 delivered 类事件.
//
// deliveredEvent 是该子类型的投递事件名(message.delivered / message.req_delivered /
// message.stream_open_delivered 等),kernel 不解析 envelope----__req_id__/
// __stream_id__/event 等都在 data 里原样透传,python 侧 demux.
//
// 各 message 子类型字段自洽,不靠 topic 耦合.kernel 对所有 message.* 一视同仁地
// dumb forward.
func (m *Manager) OnMessage(targetIDs []string, sourceID, deliveredEvent string, data any) {
	for _, tid := range targetIDs {
		if tid == "" {
			continue
		}
		clientID := m.clientIDOfNode(tid)
		if clientID == "" {
			continue
		}
		delivered := map[string]any{
			"event":     deliveredEvent,
			"target_id": tid,
			"source":    map[string]any{"id": sourceID},
		}
		if data != nil {
			delivered["data"] = data
		}
		m.sendOut(clientID, delivered)
	}
}

// OnRoutineYield 把 child 发来的 routine.yield{id, data, is_final, error?}
// 改名 routine.yielded 发回 parent.parent 按 id(child_id)查 nodes 找
// 它的父 routine 的 client----child 和 parent 可能不同 server(a@A submit b@B,
// b yield 给 a).用 parent 的 client 跨 server 转发.
func (m *Manager) OnRoutineYield(id string, data any, isFinal bool, errMsg string) {
	rid, err := strconv.Atoi(id)
	if err != nil {
		return
	}
	m.mu.Lock()
	n := m.nodes[rid]
	clientID := ""
	if n != nil && n.parent != nil {
		clientID = n.parent.clientID
	}
	m.mu.Unlock()
	if clientID == "" {
		return
	}
	delivered := map[string]any{
		"event": conn.RoutineYielded,
		"id":    id,
	}
	if data != nil {
		delivered["data"] = data
	}
	if isFinal {
		delivered["is_final"] = true
	}
	if errMsg != "" {
		delivered["error"] = errMsg
	}
	m.sendOut(clientID, delivered)
}
