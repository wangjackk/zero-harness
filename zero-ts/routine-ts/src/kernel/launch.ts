/** launch ---- kernel 句柄与组装入口: kernel.create / connect.
 * 移植 routine-py/routine/kernel/launch.py.
 *
 * 用法::
 *
 *     import { kernel } from 'routine-ts'
 *
 *     // dict 模块表 → 起进程内 kernel(listen 可选,开 grpc dial-in 口)
 *     const kernelRef = await kernel.create(modules, { listen: '0.0.0.0:52051' })
 *
 *     // str 地址 → 远程 kernel 引用(jetson / IPython 拨入场景,不建本地实体)
 *     const kernelRef2 = await kernel.create('127.0.0.1:52051')
 *
 *     const hub = new RoutineHub(routines)
 *     const conn = await hub.connect(kernelRef)  // inproc: link 绑定 + catalog push;
 *                                                 // remote: grpc transport + 重连 + catalog push
 *     ...
 *     await conn.close()  // 级联关停(grpc server → kernel → link / transport)
 */
import { GrpcClientTransport } from '../grpcClient'
import type { RoutineHub } from '../hub'
import type { Transport } from '../transport'
import { GrpcKernelServer } from '../grpcServer'
import { InProcessLink, InProcessTransport } from '../inproc'
import { Kernel, Tree } from './index'

/** hub.connect() 接受的 kernel 句柄基类(进程内实体或远程引用). */
export abstract class KernelRef {}

/** 进程内 kernel 实体(kernel.create(modules) 产物). */
export class InprocKernel extends KernelRef {
  constructor(readonly kernel: Kernel, readonly link: InProcessLink,
              readonly grpcServer: GrpcKernelServer | null = null) {
    super()
  }

  /** grpc dial-in 监听地址(未开 listen 则 null). */
  get address(): string | null {
    return this.grpcServer ? this.grpcServer.address : null
  }
}

/** 远程 kernel 引用(kernel.remote(addr) 产物);hub.connect 时经 grpc 拨入. */
export class RemoteKernel extends KernelRef {
  constructor(readonly address: string) {
    super()
  }
}

/**
 * 起进程内 kernel.``modules`` 是 flat 模块表 ``{id: {"name": ..., "children":
 * [...]}}``(根固定 'root');传入 str 等价 ``remote(str)``;``listen`` 给出则
 * 再开 grpc dial-in 口,返回地址在 ``.address``.启动后接 ``hub.connect()``
 * 完成 hub 侧组装.
 */
export async function create(
  modules: Record<string, any> | string | null = null,
  opts: { listen?: string } = {},
): Promise<KernelRef> {
  if (typeof modules === 'string') return remote(modules)
  const tree = Tree.fromDict({ root: 'root', modules: modules ?? { root: {} } })
  const kernel = new Kernel(tree)
  const link = new InProcessLink()
  kernel.bind(link)
  let grpcServer: GrpcKernelServer | null = null
  if (opts.listen) {
    grpcServer = new GrpcKernelServer(kernel, opts.listen)
    await grpcServer.start()
  }
  await kernel.start()
  return new InprocKernel(kernel, link, grpcServer)
}

/** 远程 kernel 引用(不建本地实体);hub.connect 时经 grpc 拨入. */
export function remote(address: string): RemoteKernel {
  return new RemoteKernel(address)
}

/** hub.connect() 的产物:hub ↔ kernel 连接持有体,``close()`` 关闭连接. */
export class Connection {
  constructor(readonly hub: RoutineHub, readonly kernel: KernelRef,
              readonly transport: Transport) {}

  /** 关闭连接: kernel → grpc server → link(inproc)/ transport(dial).
   *
   *  kernel 只对**同进程** hub 的节点请求-确认(见 manager.stopNode): 那批 stop
   *  必须趁 link 还活着发出去, 所以 kernel 在 link 之前。跨进程 hub 的节点只结算
   *  kernel 侧的投影, 由对端在断链时自清 —— grpc 的关闭本身就是那条通告, 不需要
   *  kernel 逐个节点去等确认。 */
  async close(): Promise<void> {
    const k = this.kernel
    if (k instanceof InprocKernel) {
      await k.kernel.stop()
      if (k.grpcServer) {
        // 0.5s: peer 是不断重连的 dial-in 客户端, 不会自己关流. 退出路上陪它
        // 吃满 2s 只换来更慢的关停.
        await k.grpcServer.stop(500)
      }
      await k.link.stop()
    } else {
      await this.transport.close()
    }
  }
}

/** 组装 hub ↔ kernel(RoutineHub.connect 的实现体,也可直接调用).
 *
 * - inproc: link 双向绑定 + worker 起 + catalog push(启动序列: bind →
 *   kernel.start(create 内) → link.start → push).
 * - remote: GrpcClientTransport 拨入(post_connect 自动拉 module.tree +
 *   catalog push,断线自动重连).
 */
export async function connect(hub: RoutineHub, kernelRef: KernelRef): Promise<Connection> {
  if (kernelRef instanceof InprocKernel) {
    const transport = new InProcessTransport(kernelRef.link)
    hub.transport = transport
    kernelRef.link.bind(kernelRef.kernel, hub.dispatchInbound.bind(hub))
    await kernelRef.link.start()
    await hub.sendCatalogPush()
  } else {
    const transport = new GrpcClientTransport((kernelRef as RemoteKernel).address)
    transport.attach(hub)
    // 断线时 hub 清理本地 handle + 销毁本地实例; await 销毁完成才放行
    // 重连(否则销毁期与重建期重叠, rid 撞车误杀新实例).
    transport.setPeerDown(async peerId => { await hub.handlePeerDown(peerId) })
    hub.transport = transport
    await transport.start()
    await transport.waitReady()
  }
  return new Connection(hub, kernelRef, hub.transport!)
}

/** ``kernel`` 命名空间(create/remote),对齐 py 侧 ``from routine import kernel``. */
export const kernel = { create, remote }
