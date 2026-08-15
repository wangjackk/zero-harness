# kernel —— Go 调度核心 + 常驻进程入口

`command`(生命周期)+ `module`(冲突校验)+ `shell`(编排)+
`grpc`(gRPC server/client)+ `conn`(wire 事件)+ `bus` + `logger`(结构化日志)。
**常驻进程入口 `main.go` 也在这里**(`package main`)。

## 启动

地址一律走同目录 `config.yaml`,CLI 不再覆盖。

```bash
cd kernel
go run .         # 常驻:按 config 起 as_grpc_server(bind :8888 等 routine 进程拨入)
go run . demo    # 跑一遍 16 步端到端 demo 后退出(连 config 第一个 as_grpc_client)
go run . xsa     # 跨 server 端到端(连 config 所有 as_grpc_client)
```

- **常驻模式**(默认):`as_grpc_server.enable: true` 时 kernel 当 gRPC server,
  routine 进程(python `start_client` / go / rust SDK)主动拨入;`as_grpc_client`
  段配置了地址则 kernel 反向连 routine server,两者可并存。
- **demo 模式**:跑 16 步端到端场景后退出,用于自测。需要 `as_grpc_client`
  指向一个已起的 routine server。

## 结构

- `main.go` —— 常驻进程入口(解析子命令 + 加载模块树 + 按 config 起 server/client;`runScenario` 同文件)
- `config.go` / `config.yaml` —— 启动配置(as_grpc_server / as_grpc_client 两段)
- `tree.json` —— 模块树配置(main.go 加载)
- `command/` —— routine 生命周期命令
- `module/` —— 模块冲突校验
- `shell/` —— 编排(manager / handler / passive auto-start)
- `grpc/` —— gRPC server + client + 生成代码(`routine.proto`)
- `conn/` —— wire 事件与 topics
- `bus/` —— 进程内事件总线
- `logger/` —— 结构化日志(`2006-01-02 15:04:05.000 INFO Name - msg file.go:line`,ANSI 着色)
