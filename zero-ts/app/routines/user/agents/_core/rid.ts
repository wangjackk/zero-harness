/** agent rid 解析 helper -- 直接两跳快查询, 不骑 routine 生命周期.
 *
 * 前端聊天链路等需要按 agent_id 查目标 agent 的 rid:
 *   1. getRunningRoutines() -- transport/kernel 直接查询, 不起 routine
 *   2. req(manager_rid, 'live_agents') -- p2p 一跳
 */
import type { Routine } from 'routine-ts'

/** (manager routine name, agent_type) -- 各类型 agent 的常驻 manager 清单. */
const MANAGERS: [string, string][] = [
  ['pi/agent/manager', 'pi'],
]

/** passive 常驻 agent: routine name 即 agent_id 即 agent_type. */
const PASSIVE_AGENTS: [string, string, string][] = [
  ['user_agent', 'user', 'user'],
  ['world_agent', 'world', 'world'],
]

/** 按 agent_id 解析 (rid, agent_type). 未找到返回 (null, null). */
export async function resolveAgentRid(routine: Routine,
                                      agentId: string): Promise<[string | null, string | null]> {
  const routines = await routine.getRunningRoutines()
  const nameToRid = new Map<string, string>()
  for (const r of routines) {
    nameToRid.set(String(r?.name ?? ''), String(r?.id ?? ''))
  }

  // passive 常驻 agent: 直接命中, 不需要问 manager.
  for (const [routineName, passiveId, agentType] of PASSIVE_AGENTS) {
    if (agentId === passiveId) {
      const rid = nameToRid.get(routineName) ?? ''
      return rid ? [rid, agentType] : [null, null]
    }
  }

  for (const [managerName, agentType] of MANAGERS) {
    const rid = nameToRid.get(managerName)
    if (!rid) continue
    let resp: any
    try {
      // live_agents: manager 纯内存快照(不碰存储), 热路径快.
      resp = await routine.req(rid, 'live_agents', {}, 5.0)
    } catch { continue }
    for (const item of resp?.agents ?? []) {
      if (item?.agent_id === agentId && item?.routine_id) {
        return [String(item.routine_id), agentType]
      }
    }
  }
  return [null, null]
}

/** 列出全部 live agent [{agent_id, routine_id, agent_type}]. */
export async function resolveAllAgents(routine: Routine): Promise<any[]> {
  const routines = await routine.getRunningRoutines()
  const nameToRid = new Map<string, string>()
  for (const r of routines) {
    nameToRid.set(String(r?.name ?? ''), String(r?.id ?? ''))
  }
  const agents: any[] = []
  for (const [managerName, agentType] of MANAGERS) {
    const rid = nameToRid.get(managerName)
    if (!rid) continue
    let resp: any
    try { resp = await routine.req(rid, 'live_agents', {}, 5.0) } catch { continue }
    for (const it of resp?.agents ?? []) {
      if (it?.routine_id) {
        agents.push({ agent_id: it.agent_id, routine_id: it.routine_id, agent_type: agentType })
      }
    }
  }
  for (const [routineName, passiveId, agentType] of PASSIVE_AGENTS) {
    const rid = nameToRid.get(routineName)
    if (rid) agents.push({ agent_id: passiveId, routine_id: rid, agent_type: agentType })
  }
  return agents
}
