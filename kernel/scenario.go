package main

import (
	"fmt"
	"time"

	"kernel/command"
	"kernel/shell"
)

// runScenario 跑 16 步端到端场景.
// 经 `go run . demo` 触发,输出走 fmt(测试报告样式,非运行时日志).
// routine 的 client 由 Create 按 name 查路由表定(catalog 拉取时注册)----demo 单
// server,所有 routine 都在该 client.
func runScenario(m *shell.Manager) {
	report := func(cmd *command.Command, err error) {
		if err != nil {
			fmt.Printf("  -> REJECTED: %v\n", err)
			return
		}
		fmt.Printf("  -> %s state=%s modules=%v\n", cmd, cmd.State(), cmd.Modules)
	}

	fmt.Println("1) Execute serve [body]")
	serve, err := m.Execute(m.RootID(), "serve", nil)
	report(serve, err)

	fmt.Println("2) Execute intruder [body] -- expect conflict")
	c2, err := m.Execute(m.RootID(), "intruder", nil)
	report(c2, err)

	fmt.Println("3) Execute pour [leg] under serve -- expect ok (子真占 leg,不扣父覆盖)")
	pour, err := m.Execute(serve.ID, "pour", nil)
	report(pour, err)

	fmt.Println("5) Execute speak [mouth] under serve -- expect modules=[mouth]")
	c3, err := m.Execute(serve.ID, "speak", nil)
	report(c3, err)

	fmt.Println("6) Execute other [mouth] -- expect conflict (speak holds it)")
	c4, err := m.Execute(m.RootID(), "other", nil)
	report(c4, err)

	fmt.Println("7) Stop serve -- cascade: pour, speak, then serve")
	m.Stop(serve.ID)

	fmt.Println("8) Execute after [body] -- expect ok (body released)")
	c5, err := m.Execute(m.RootID(), "after", nil)
	report(c5, err)
	if c5 != nil {
		m.Stop(c5.ID)
	}

	fmt.Println("9) Execute compose -- submit quick, start, wait (routine 调 routine 经 kernel 回环)")
	c6, err := m.Execute(m.RootID(), "compose", nil)
	report(c6, err)
	// compose 在 start 里 submit quick → start → wait,自然完成
	if c6 != nil {
		c6.Wait()
		fmt.Printf("  -> compose finished state=%s\n", c6.State())
	}

	fmt.Println("10) Cascade: top→mid→leaf,主动 Stop(top) 验证级联")
	top, err := m.Execute(m.RootID(), "cascade_top", nil)
	report(top, err)
	// 给 top→mid→leaf 都起来(submit + start + handle.wait 链)的时间
	time.Sleep(time.Second)
	fmt.Println("   Stop(top) -- 期望级联停 mid, leaf")
	m.Stop(top.ID)
	fmt.Printf("  -> top state=%s\n", top.State())

	fmt.Println("11) Execute asker -- submit builder, req 'build' (message.req 经 kernel 中转)")
	asker, err := m.Execute(m.RootID(), "asker", nil)
	report(asker, err)
	if asker != nil {
		asker.Wait()
		fmt.Printf("  -> asker finished state=%s\n", asker.State())
	}

	fmt.Println("12) Execute tick_listener + tick_publisher -- pubsub 经 kernel fanout")
	listener, err := m.Execute(m.RootID(), "tick_listener", nil)
	report(listener, err)
	if listener != nil {
		// 给 listener 的 @subscribe('tick') 经 kernel 注册好
		time.Sleep(200 * time.Millisecond)
	}
	pub, err := m.Execute(m.RootID(), "tick_publisher", nil)
	report(pub, err)
	if pub != nil {
		pub.Wait()
		fmt.Printf("  -> tick_publisher finished state=%s\n", pub.State())
	}
	if listener != nil {
		m.Stop(listener.ID)
	}

	fmt.Println("13) Execute stream_collector -- submit stream_gen, async for 收 yield(body upstream 经 kernel 中转)")
	sc, err := m.Execute(m.RootID(), "stream_collector", nil)
	report(sc, err)
	if sc != nil {
		sc.Wait()
		fmt.Printf("  -> stream_collector finished state=%s\n", sc.State())
	}

	fmt.Println("14) Execute message_sender -- submit receiver, send 3 条 (pre-start 投递), start")
	is, err := m.Execute(m.RootID(), "message_sender", nil)
	report(is, err)
	if is != nil {
		is.Wait()
		fmt.Printf("  -> message_sender finished state=%s\n", is.State())
	}

	// 15) 父子共占同一模块:父 hold [body],子也声明 [body] → holders 队列叠加 [父,子].
	// 外人占 body 仍被挡(队里有父或子).父 stop 后子还占,外人仍挡;子也 stop 才释放.
	fmt.Println("15) 父子共占 body -- serve 持 body,子 reusabody 也声明 body,holders 叠加")
	serve2, err := m.Execute(m.RootID(), "serve", nil)
	report(serve2, err)
	if serve2 != nil {
		// 子声明 body(父已占)----TryAcquire 跳过父祖先,允许共占
		child, err := m.Execute(serve2.ID, "serve", nil)
		report(child, err)
		if child != nil {
			// 外人占 body:被 serve2 或 child 挡(holders=[serve2,child])
			intr, err := m.Execute(m.RootID(), "intruder", nil)
			report(intr, err)
			fmt.Println("   Stop(serve2) -- 级联停 child,然后 serve2,body 才释放")
			m.Stop(serve2.ID)
		}
	}

	// 16) 运行时占领 + cmd.Modules 同步 + kernel 自动释放:
	// Acquirer 静态 modules()=[],start 里 acquire ['body'] 不 release.
	// 验证:① acquire 后 cmd.Modules 同步成 [body];② routine 结束后 kernel 全量
	// Release(rid) 自动清 holders(不泄漏)----外人 Execute [body] 应 ok.
	fmt.Println("16) 运行时 acquire -- cmd.Modules 同步 + 结束后 kernel 自动释放")
	acq, err := m.Execute(m.RootID(), "acquirer", nil)
	report(acq, err)
	if acq != nil {
		// 等 acquire 发生(start 体里),然后查 cmd.Modules 是否同步
		time.Sleep(200 * time.Millisecond)
		fmt.Printf("  -> acquirer acquired 后 cmd.Modules=%v (应含 body)\n", acq.ModulesList())
		acq.Wait()
		fmt.Printf("  -> acquirer finished state=%s (holders 已被 kernel 自动释放)\n", acq.State())
		// 外人占 body 验证 holders 已释放(没泄漏)
		reuse, err := m.Execute(m.RootID(), "serve", nil)
		report(reuse, err)
		if reuse != nil {
			m.Stop(reuse.ID)
		}
	}
}
