package main

import (
	"os"

	"gopkg.in/yaml.v3"
)

// asGrpcServerConfig:kernel 作为 grpc server,routine 进程主动拨入.
// Enable=false 或整段缺省 = 不起 server(纯 as_grpc_client,向后兼容).
type asGrpcServerConfig struct {
	Enable  bool   `yaml:"enable"`
	Address string `yaml:"address"`
}

// clientTarget:kernel 作为 grpc client 主动连的 routine server 地址.
type clientTarget struct {
	Address string `yaml:"address"`
}

// kernelConfig 对标老版 routine_server.server_list,扩展支持 kernel 同时作为
// grpc server(as_grpc_server)+ grpc client(as_grpc_client).两段独立----
// 都配 = kernel 既 bind 监听(接受 routine 拨入)又 connect(主动连 routine server);
// 只配一段 = 纯一方向.
type kernelConfig struct {
	AsGrpcServer *asGrpcServerConfig `yaml:"as_grpc_server"`
	AsGrpcClient []clientTarget      `yaml:"as_grpc_client"`
}

// loadConfig 加载 config.yaml.as_grpc_client 段缺省,显式空 [],或文件不存在
// 都 = 不连任何 server(空列表);只有显式写地址才连.不回退任何 fallback----
// 要连谁就在 config 里写.
//
// as_grpc_server:Enable=false 或段缺省 = 不起 server.
func loadConfig() *kernelConfig {
	data, err := os.ReadFile("config.yaml")
	if err != nil {
		// 文件不存在 = 什么都不起(纯 server 模式靠 config 开启;无 config 文件
		// = 干跑 kernel,不连不监听).
		return &kernelConfig{}
	}
	var cfg kernelConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return &kernelConfig{}
	}
	return &cfg
}

// loadClientAddrs 返回 as_grpc_client 地址列表.无 config / 段缺省 / [] 都返回空
// (不连).demo/xsa 短命脚本也走 config,不再认 CLI addr 作 fallback.
func loadClientAddrs() []string {
	cfg := loadConfig()
	addrs := make([]string, 0, len(cfg.AsGrpcClient))
	for _, c := range cfg.AsGrpcClient {
		if c.Address != "" {
			addrs = append(addrs, c.Address)
		}
	}
	return addrs
}
