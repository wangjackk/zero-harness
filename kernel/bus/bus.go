// Package bus 进程内事件总线:通用 pub/sub,载荷 any,不认识任何域类型.
//
// 把 conn 的入站事件(Stream Recv 来的 msg)和连接生命周期(up/down)扇出给多个
// 订阅者----编排(shell.Manager)+ 可视化/tracer + 将来的监控旁路,各自独立消费,
// 互不阻塞.这是 conn 抽象的关键抹平层:dial-out 和 dial-in 的 reader 都往同一条
// bus publish,上层订阅一次就覆盖两种来源,不必关心 conn 方向.
//
// topic 名与载荷类型由调用方域定义(kernel/conn 定义 TopicEvent/TopicConn +
// EventIn/ConnChange).bus 只管扇出,不解释载荷.
//
// 语义:fire-and-forget.Publish 非阻塞投递到每个订阅者的 buffered chan,订阅者
// 消费慢导致 buffer 满则按订阅者的 drop 策略处理----drop=true 丢弃+warn(旁路用),
// drop=false 阻塞 publisher(编排用,保证不丢).顺序保证:单订阅者收到的顺序 =
// publish 顺序.
package bus

import (
	"sync"

	"kernel/logger"
)

// Subscriber 订阅者:独立 buffered chan,消费 topic 的载荷.
type Subscriber struct {
	topic string
	ch    chan any
	drop  bool // true: 满则丢+warn(旁路用);false: 满则阻塞(编排用,保证不丢)
	bus   *Bus
	log   *logger.Logger
}

// Recv 返回载荷 chan(消费者 for-range 或 select).
func (s *Subscriber) Recv() <-chan any { return s.ch }

// Close 关闭订阅,从 bus 摘除.订阅者用完应调(防泄漏).
func (s *Subscriber) Close() {
	s.bus.unsubscribe(s)
}

// Bus 进程内单例事件总线.
type Bus struct {
	mu   sync.Mutex
	subs map[string][]*Subscriber
	log  *logger.Logger
}

var (
	busOnce sync.Once
	busInst *Bus
)

// GetBus 单例.
func GetBus() *Bus {
	busOnce.Do(func() {
		busInst = &Bus{
			subs: map[string][]*Subscriber{},
			log:  logger.GetLogger().Named("bus"),
		}
	})
	return busInst
}

// Subscribe 订阅 topic.bufSize=chan 容量;drop=true 时满则丢+warn(旁路用),
// false 时满则阻塞 publish(编排用,保证不丢----但 publisher 会等待,reader 慢消费
// 阻塞).编排用大 buf + drop=false;tracer 用中等 buf + drop=true.
func (b *Bus) Subscribe(topic string, bufSize int, drop bool) *Subscriber {
	s := &Subscriber{
		topic: topic,
		ch:    make(chan any, bufSize),
		drop:  drop,
		bus:   b,
		log:   b.log,
	}
	b.mu.Lock()
	b.subs[topic] = append(b.subs[topic], s)
	b.mu.Unlock()
	return s
}

// Publish 扇出 payload 给 topic 的所有订阅者.
func (b *Bus) Publish(topic string, payload any) {
	b.mu.Lock()
	subs := b.subs[topic]
	b.mu.Unlock()
	for _, s := range subs {
		s.deliver(payload)
	}
}

func (s *Subscriber) deliver(payload any) {
	if s.drop {
		select {
		case s.ch <- payload:
		default:
			s.log.Warnf("subscriber %s buffer full, dropped event", s.topic)
		}
		return
	}
	// 阻塞模式:编排路径必须不丢.publisher(reader)会等.
	s.ch <- payload
}

func (b *Bus) unsubscribe(s *Subscriber) {
	b.mu.Lock()
	defer b.mu.Unlock()
	list := b.subs[s.topic]
	for i, x := range list {
		if x == s {
			b.subs[s.topic] = append(list[:i], list[i+1:]...)
			break
		}
	}
}
