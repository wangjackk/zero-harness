package grpc

import (
	"kernel/bus"
	"kernel/conn"
	"kernel/logger"
)

// reader 持续读 Stream 回报,把每条 msg publish 到 bus(conn.event).
//
// 纯传输:零 future 知识(created/started/stopped 回执的 chan 解析在 shell.Manager
// dispatchEvent 里,chan 在 shell.node 上)+ 零业务知识(acquire/message/pubsub/relay/
// routine.submit 等分发在 Manager).reader 只做 Recv→Publish----这是 conn 抽象的
// 关键:gRPC 实现层不认识任何域语义.
//
// created 期间 stopped(created 失败)也照常 publish:Manager dispatchEvent 按
// cmd.State()==StateCreated 判定为 created 失败,resolve createdCh with err 且跳过
// 级联清理(caller 处理)----reader 不必区分.
func (c *Client) reader() {
	for {
		m, err := c.stream.Recv()
		if err != nil {
			c.onStreamError()
			return
		}
		msg, err := frameToMap(m)
		if err != nil {
			// 坏帧跳过(报错暴露),连接本身还活着
			logger.GetLogger().Named("rpc").Errorf("client %s bad frame: %v", c.id, err)
			continue
		}
		bus.GetBus().Publish(conn.TopicEvent, conn.EventIn{ConnID: c.id, Msg: msg})
	}
}
