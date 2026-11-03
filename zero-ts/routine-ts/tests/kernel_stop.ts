/** kernel.stop 的等/不等按宿主分(对齐 py routine-py/tests/test_kernel_stop.py)。
 *
 * 运行: `bun run tests/kernel_stop.ts`
 *
 * 钉住的语义:
 * 1. kernel.stop 对跨进程 hub(grpc peer)只结算自己的投影 —— 不发
 *    lifecycle.stop、不等确认(对端在断链时自清, 见 hub.handlePeerDown);
 *    正常显式 stop 则必须向存活远程 owner 发 stop 并等待 STOPPED;
 * 2. 同进程 hub(inproc link)必须请求-确认: 发 lifecycle.stop, 回报前不许摘
 *    投影(摘了就是和 hub 侧的 stop hook 赛跑);
 * 3. kernel.stop() 还要收 root 子树之外的节点(顶层 create / 父先走留下的),
 *    否则它们的 cmd body 挂到事件循环关闭。
 */
import { create, InprocKernel } from '../src/kernel/launch'
import { LIFECYCLE_STOP, ROUTINE_STOP, TerminationReason, terminationReasonFromWire } from '../src/protocol'
import { RoutineHandle } from '../src/handle'
import { GrpcKernelServer } from '../src/grpcServer'
import { EventEmitter } from 'node:events'

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

async function waitFor(pred: () => boolean, what: string): Promise<void> {
  for (let i = 0; i < 250; i++) {
    if (pred()) return
    await sleep(20)
  }
  throw new Error(`timeout waiting for ${what}`)
}

async function boot(connId: string, local: boolean) {
  const ref = (await create(null)) as InprocKernel
  const k: any = ref.kernel
  k.STOP_TIMEOUT = 0.3
  const sent: any[] = []
  k._conns.set(connId, {
    id: connId, local, hubId: 'hubX', send: (m: any) => sent.push(m),
  })
  k._hubConns.set('hubX', connId)
  k.routes.set('n1', { owner: 'hubX' })
  const top = k.onCreate(0, 'n1', {})   // 顶层: 父链不在 root 子树
  k.startRoutine(top)
  await k.onLifecycleCreated({ id: String(top) }, connId)
  await k.onLifecycleStarted({ id: String(top) }, connId)
  await sleep(30)
  return { k, sent, top }
}

// 1) 跨进程 hub: 只结算投影
{
  const { k, sent } = await boot('sA', false)
  await k.stop()
  const stops = sent.filter(m => m.event === LIFECYCLE_STOP)
  if (stops.length) {
    throw new Error(`peer hub 不该收到 lifecycle.stop: ${JSON.stringify(stops)}`)
  }
  if (k.nodes.size) throw new Error(`nodes 该清空, 残留 ${[...k.nodes.keys()]}`)
  console.log('peer hub shutdown: PASS')
}

// 2) 正常显式 stop: 远程 owner 存活时同样请求-确认,不能按 DISCONNECTED 结算
{
  const { k, sent, top } = await boot('sB', false)
  const n = k.nodes.get(top)
  await k.dispatch({ event: ROUTINE_STOP, child_id: String(top) }, 'sB')
  await waitFor(() => sent.some(m => m.event === LIFECYCLE_STOP),
                'lifecycle.stop sent for explicit remote stop')
  if (!k.nodes.has(top) || n.stoppedFut.done) {
    throw new Error('远程 owner 回报前不该结算或删除投影')
  }
  await k.onLifecycleStopped({ id: String(top), reason: 'STOPPED' }, 'sB')
  await waitFor(() => !k.nodes.has(top), 'remote stopped projection removed')
  const stopped = await n.stoppedFut.promise
  if (stopped.reason !== 'STOPPED' || stopped.error) {
    throw new Error(`显式 stop 元数据错误: ${JSON.stringify(stopped)}`)
  }
  await k.stop()
  console.log('peer explicit stop: PASS')
}

// 3) 同进程 hub: 请求-确认
{
  const { k, sent, top } = await boot('local', true)
  const stopper = k.stop()
  await sleep(80)
  if (!sent.some(m => m.event === LIFECYCLE_STOP)) {
    throw new Error('local hub 没收到 lifecycle.stop')
  }
  if (!k.nodes.has(top)) {
    throw new Error('回报前就摘了节点: 会与 stop hook 赛跑')
  }
  await k.onLifecycleStopped({ id: String(top), reason: 'STOPPED' }, 'local')
  await stopper
  if (k.nodes.size) throw new Error(`nodes 该清空, 残留 ${[...k.nodes.keys()]}`)
  console.log('local hub: PASS')
}

// 4) 终止原因与父侧元数据契约
{
  if (terminationReasonFromWire('legacy') !== TerminationReason.UNKNOWN) {
    throw new Error('未知 wire reason 必须归为 UNKNOWN')
  }
  const h = new RoutineHandle('child', 'worker')
  h.notifyDone(undefined, null, TerminationReason.PREEMPTED, 'evictor', 'evicted by evictor')
  if (h.reason !== TerminationReason.PREEMPTED || h.by !== 'evictor'
      || h.detail !== 'evicted by evictor') {
    throw new Error(`handle 终止元数据不一致: ${h.reason}/${h.by}/${h.detail}`)
  }
  if (await h.wait() !== undefined) throw new Error('PREEMPTED 不应改变 wait 返回策略')
  console.log('termination metadata: PASS')
}

// 5) grpc 对端取消: cancelled 必须走同一断连收尾,注销 conn 并放行队列 worker
{
  const ref = (await create(null)) as InprocKernel
  const k: any = ref.kernel
  const server: any = new GrpcKernelServer(k, 'unused')
  const before = k._conns.size
  const call: any = new EventEmitter()
  call.write = () => true
  call.end = () => {}
  server._onStream(call)
  if (k._conns.size !== before + 1) throw new Error('grpc stream 应注册一个 conn')
  call.emit('cancelled')
  await sleep(0)
  if (k._conns.size !== before) throw new Error('cancelled 后 grpc conn 必须注销')
  await k.stop()
  console.log('grpc cancelled cleanup: PASS')
}

console.log('ALL PASS')
