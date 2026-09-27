/** 共享: 向 WS bridge 注册自己 (agent_id/namespace/name), 轮询直到成功.
 * 移植 _core/bridge.py.
 *
 * prime/reactor 共用实现, 唯一差异是 name 前缀
 * 和注册成功后的回调 (emitSessionHistory vs emitSessionChanged).
 */
import { ReqError, ReqTimeout } from 'routine-ts/errors'
import { sleep } from 'routine-ts/async'
import type { Routine } from 'routine-ts'

export async function registerWithBridge(opts: {
  agentId: string
  routineId: string
  namePrefix: string
  bridgeName: string
  ctx: Routine
  stopEvent: { isSet: boolean }
  logger: { info(msg: string): void; warning(msg: string): void }
  onSuccess?: () => Promise<void>
  retryInterval?: number
}): Promise<void> {
  const {
    agentId, routineId, namePrefix, bridgeName, ctx, stopEvent,
    logger, onSuccess, retryInterval = 0.2,
  } = opts

  const payload = {
    agent_id: agentId,
    namespace: agentId,
    name: `${namePrefix}${agentId.slice(0, 8)}`,
    routine_id: routineId,
  }

  while (!stopEvent.isSet) {
    let routines: any[] = []
    try {
      routines = await ctx.getRunningRoutines()
    } catch (exc: any) {
      logger.warning(`get_running failed (${exc?.message ?? exc}), retry`)
    }
    let bridgeId: string | null = null
    for (const r of routines) {
      if (String(r?.name ?? '') === bridgeName) {
        bridgeId = String(r?.id ?? '') || null
        break
      }
    }
    if (bridgeId !== null) {
      try {
        await ctx.req(bridgeId, 'register_agent', payload, 2.0)
        logger.info(`registered with bridge (bridge_id=${bridgeId})`)
        if (onSuccess) await onSuccess()
        return
      } catch (exc: any) {
        if (exc instanceof ReqTimeout || exc instanceof ReqError) {
          logger.warning(`register req failed (${exc?.message ?? exc}), retry`)
        } else {
          throw exc
        }
      }
    }
    await sleep(retryInterval)
  }
}
