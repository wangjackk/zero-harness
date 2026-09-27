/** zero-ts 应用入口: 拨入 py 侧 kernel, 进程外 gRPC 连接.

 * py 侧 (zero-py) 拥有 kernel 实体 (grpc server 监听 7795) 与全部基础设施
 * routine (web_server / send_message / ipython / skills ...). 本进程经
 * kernel.remote 拿远程引用, hub.connect 拨入后注册 TS 侧独有 routine
 * (pi 引擎包), 文件事件桥/工具调用经共享 kernel 跨语言路由.
 *
 * 静态只注册 RoutinesLoader(引导扇区), catalog push 后 kernel auto-start
 * 按 routines.yaml 运行时注册其余 routine.
 * ZERO_KERNEL_ADDR 覆盖 kernel 地址.
 *
 * 运行(在 zero-ts/ 目录下): ``bun run app/main.ts``
 */
import { Routines, RoutineHub, kernel } from 'routine-ts'
import { setupLogger } from 'routine-ts/logger'

import { RoutinesLoader } from './routines/loader'

const log = setupLogger('app')

const DEFAULT_KERNEL_ADDR = '127.0.0.1:7777'

async function main(): Promise<void> {
  const rs = new Routines()
  rs.register(RoutinesLoader)

  const kernelAddr = process.env.ZERO_KERNEL_ADDR || DEFAULT_KERNEL_ADDR
  const kernelRef = kernel.remote(kernelAddr)
  const hub = new RoutineHub(rs, null, [], 'zero-ts')
  const conn = await hub.connect(kernelRef)
  log.info(`zero-ts app started (dial-in kernel ${kernelAddr}, loader)`)

  const shutdown = () => {
    void (async () => {
      await conn.stop()
      process.exit(0)
    })()
  }
  process.on('SIGINT', shutdown)
  process.on('SIGTERM', shutdown)
  await new Promise<void>(() => {})  // 挂住: transport 重连循环持续服务
}

void main()
