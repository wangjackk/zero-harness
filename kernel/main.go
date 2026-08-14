// Command kernel 是 kshell 调度器常驻进程入口.
//
// 流程:加载模块树 → 加载 config.yaml → 按 config 起 as_grpc_server(kernel 当
// server,bind+accept routine 拨入)和/或 as_grpc_client(kernel 当 client,connect
// routine server)→ 起 shell.Manager 处理 lifecycle/submit/start/stop → 阻塞等信号退出.
//
// 启动:
//
//	cd kernel
//	go run .         # 常驻(config.yaml:as_grpc_server + as_grpc_client 可并存)
//	go run . demo    # 跑一遍 16 步端到端 demo 后退出(连 config 第一个 as_grpc_client)
//	go run . xsa     # 跨 server 端到端(连 config 所有 as_grpc_client)
//
// 地址一律走 config.yaml,CLI 不再覆盖.前置:as_grpc_client 模式下 python 侧 routine
// server 起在 as_grpc_client 段 address;as_grpc_server 模式下 python 侧 routine 用
// start_client 拨 as_grpc_server.address.模块树从同目录 tree.json 加载.
package main

import (
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"kernel/grpc"
	"kernel/logger"
	"kernel/module"
	"kernel/shell"
)

func main() {
	log := logger.GetLogger().Named("INIT")
	log.Infof("PID: %d", os.Getpid())

	// 解析参数:子命令选模式,地址一律走 config.yaml(CLI 不再覆盖).
	//	go run .         → 常驻(config.yaml:as_grpc_server + as_grpc_client 可并存)
	//	go run . demo    → 跑 demo 后退出(连 config 第一个 as_grpc_client)
	//	go run . xsa     → 跨 server 端到端(连 config 所有 as_grpc_client)
	demo := false
	xsa := false
	for _, a := range os.Args[1:] {
		switch {
		case a == "demo" || a == "--demo":
			demo = true
		case a == "xsa":
			// 跨 server 端到端验证:连 config.yaml 的 as_grpc_client,Execute xs_a,wait.
			xsa = true
		case a == "--help" || a == "-h":
			log.Info("用法: go run . [demo|xsa]   -- demo/xsa 跑场景后退出;无则常驻(config.yaml:as_grpc_server + as_grpc_client 可并存)")
			return
		default:
			// 忽略未知参数(曾支持 CLI addr 覆盖,已移除----一律走 config).
		}
	}

	// 模块树(tree.json 同目录,cd kernel && go run . 时 cwd=kernel).
	treeRoot, err := module.LoadFile("tree.json")
	if err != nil {
		log.Errorf("加载模块树失败: %v", err)
		os.Exit(1)
	}
	module.Init(treeRoot)
	log.Info("✅🌳 module tree updated")
	fmt.Print(module.PrintTree(treeRoot))

	// shell.Manager 不再绑定单一 client----用 AddClient 逐个注册(多 server).
	m := shell.New(module.Default())

	if demo {
		// demo:连 config.yaml 的第一个 as_grpc_client(CLI addr 可覆盖,方便临时
		// `go run . demo :50052`).同步拉一次 catalog 打印(不起 passive),跑完
		// 16 步退出.不挂 reconnect 回调----demo 短命,不期望重连.
		addrs := loadClientAddrs()
		if len(addrs) == 0 {
			log.Errorf("demo 需要一个 routine server:config.yaml 未配 as_grpc_client,也没给 CLI addr")
			os.Exit(1)
		}
		runDemo(m, log, addrs[0])
		return
	}

	if xsa {
		// 跨 server 端到端:连 config.yaml 的 as_grpc_client,Execute xs_a,wait 拿 result.
		runXsA(m, log, loadClientAddrs())
		return
	}

	// 常驻:config.yaml 驱动,as_grpc_server(kernel 当 server,bind+accept)
	// + as_grpc_client(kernel 当 client,connect)可并存.两段独立----都配 =
	// kernel 既监听拨入又主动连出;只配一段 = 纯一方向(无 as_grpc_server 段 = 纯
	// client;无 as_grpc_client 段 = 纯 server).
	cfg := loadConfig()
	if cfg.AsGrpcServer != nil && cfg.AsGrpcServer.Enable {
		srv, err := grpc.NewServer(cfg.AsGrpcServer.Address, m.AddConn)
		if err != nil {
			log.Errorf("起 as_grpc_server 失败: %v", err)
		} else {
			srv.SetReqHandler(m.HandleReq) // dial-in routine->kernel Req 查询(get_running_routines)
			go func() { _ = srv.Start() }()
			log.Infof("🚀 grpc server listening %s,", srv.Address())
		}

	}
	if len(cfg.AsGrpcClient) > 0 {
		log.Infof("📋 配置 %d 个 as_grpc_client(routine server):", len(cfg.AsGrpcClient))
		for i, c := range cfg.AsGrpcClient {
			log.Infof("   [%d] %s", i+1, c.Address)
		}
		for _, c := range cfg.AsGrpcClient {
			serverAddr := c.Address
			c, err := grpc.New(serverAddr)
			if err != nil {
				log.Errorf("连接 server %s 失败: %v", serverAddr, err)
				continue
			}
			m.AddConn(c)
			// conn 生命周期经 bus 驱动:连上 → Manager lifeline goroutine 拉 catalog +
			// 起 passive;断线 → 卸载该 conn 名下 routine + 推缩小后的模块视图给剩余 conn.
			c.Start()
			log.Infof("🔗 routine server: %s (client %s)", serverAddr, c.ID())
		}
	}

	// 常驻:阻塞等信号.as_grpc_server 拨入的 routine 经 catalog.push 注册路由 +
	// as_grpc_client 连接的 routine 经 LoadCatalog 拉取注册----Manager 不区分方向,
	// Execute 统一按 name 路由.
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	log.Info("🚀 Kernel 启动成功,按 Ctrl+C 退出")
	<-sig
	log.Info("🛑 收到退出信号,关闭...")
}

// runDemo 单 server 跑 16 步场景.不依赖 bus 的 async catalog----显式 LoadCatalog sync.
func runDemo(m *shell.Manager, log *logger.Logger, addr string) {
	log.Infof("📋 routine server: %s", addr)
	c, err := grpc.New(addr)
	if err != nil {
		log.Errorf("连接 server 失败: %v", err)
		os.Exit(1)
	}
	defer c.Close()
	m.AddConn(c)
	c.Start()
	// 等 stream 真正就绪再拉 catalog(避免 lazy-connect 握手未完拉空).
	if !c.WaitReady() {
		log.Errorf("server %s 30s 内未连上", addr)
		os.Exit(1)
	}
	m.LoadCatalog(c)
	log.Info("🧪 运行 16 步端到端 demo 场景...")
	runScenario(m)
	log.Info("✅ demo 完成")
}

// runXsA 跨 server 端到端验证:连 config.yaml 的所有 as_grpc_client,Execute xs_a(在
// server A),wait 拿 result.验证 a@A submit b@B submit c@A + b req c + b yield→a
// 全跨 server(name 路由 + broker 中央化).短命,不挂 reconnect.
func runXsA(m *shell.Manager, log *logger.Logger, addrs []string) {
	log.Infof("📋 配置 %d 个 routine server:", len(addrs))
	for i, a := range addrs {
		log.Infof("   [%d] %s", i+1, a)
	}
	for _, a := range addrs {
		c, err := grpc.New(a)
		if err != nil {
			log.Errorf("连接 server %s 失败: %v", a, err)
			continue
		}
		m.AddConn(c)
		c.Start()
		// 等 stream 真正就绪再拉 catalog----c.Start() 后立即 Req 撞 lazy-connect 握手
		// 未完会拉空 → routine not registered.
		if !c.WaitReady() {
			log.Errorf("server %s 30s 内未连上,跳过", a)
			continue
		}
		m.LoadCatalog(c)
	}
	// 等 catalog 注册完(routineClients 路由表).
	log.Info("🧪 Execute xs_a(跨 server:a@A submit b@B submit c@A + b req c + b yield→a)...")
	a, err := m.Execute(m.RootID(), "xs_a", nil)
	if err != nil {
		// 不 panic----对标老版 push 失败回 error.验证脚本:失败打 ERROR + 非零退出,
		// 让调用方/CI 知道,但不走 os.Exit 跳过 defer(这里没 defer,但保持一致风格).
		log.Errorf("❌ Execute xs_a 失败: %v", err)
		os.Exit(1)
	}
	a.Wait()
	log.Infof("✅ xs_a finished state=%s", a.State())
}
