/** routines.yaml 配置驱动加载 + 引导 routine (纯静态注册).
 *
 * yaml 声明"启用哪些模块", 两种条目粒度:
 *   - 文件条目 (xxx.ts): import 该模块, 注册其 export 的 Routine 子类
 *   - 目录条目 (pkg/):   import 该包 index.ts, 注册其 re-export 的 Routine 类
 *                        ---- index.ts 即包的 manifest, ESM export 语句天然
 *                        承担 py __all__ 的声明语义(re-export 什么注册什么).
 *
 * 两种条目形态:
 *   - 纯字符串: ``- routines/user/ask.ts``
 *   - dict (带 kwargs): passive routine 的启动配置. 注册时 kwargs 覆盖式注入
 *     类的 isPassive(PassiveConfig = passive + auto-start 默认入参), 随 catalog
 *     推给 kernel; auto-start 时 Execute(name, kwargs) 带参拉起, run(kwargs)
 *     自然收到 ---- 配置随注册一次流动, routine 内无需回头读 yaml.
 *
 * 引导: RoutinesLoader 是唯一静态注册的 passive routine, kernel 自动拉起后
 * 运行时注册 yaml 里的其余全部 routine, 注册完成即自然退出(passive 无监督
 * 重拉, kernel 不会循环重启). 源码/yaml 变更不热重载 ---- 重启进程生效.
 */
import { readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import YAML from 'yaml'

import { PassiveConfig, Routine, Routines, routineNameOf } from 'routine-ts'
import type { Payload, RoutineClass } from 'routine-ts'
import { setupLogger } from 'routine-ts/logger'

const log = setupLogger('ts_routines_loader')

/** app 包根 (…/zero-ts/app). */
const APP_ROOT = resolve(import.meta.dir, '..')
const YAML_PATH = join(APP_ROOT, 'routines.yaml')

type Entry = { rel: string; kwargs: Record<string, any> }

/** yaml "routines" 列表 → 条目集. 纯字符串 → kwargs {}; dict → (path, kwargs). */
function parseEntries(data: any): Entry[] {
  const out: Entry[] = []
  for (const e of data?.routines ?? []) {
    if (e && typeof e === 'object') {
      const rel = String(e.path ?? '').trim().replaceAll('\\', '/')
      if (!rel) throw new Error('routines.yaml: dict 条目缺 path')
      if (e.kwargs != null && typeof e.kwargs !== 'object') {
        throw new Error(`${rel}: kwargs 必须是 mapping`)
      }
      out.push({ rel, kwargs: e.kwargs ?? {} })
    } else {
      out.push({ rel: String(e).trim().replaceAll('\\', '/'), kwargs: {} })
    }
  }
  return out
}

/** 读 routines.yaml 一次 → 条目集. yaml 全生命周期唯一读盘点. */
function readYamlEntries(): Entry[] {
  const data = YAML.parse(readFileSync(YAML_PATH, 'utf8')) ?? {}
  const entries = parseEntries(data)
  if (!entries.length) throw new Error(`${YAML_PATH}: "routines" 列表为空`)
  return entries
}

/** 条目 rel → 模块绝对路径(目录条目 → 其 index.ts; 文件条目 → 自身). */
function entryModulePath(rel: string): string {
  const target = join(APP_ROOT, rel)
  const st = statSync(target, { throwIfNoEntry: false })
  if (st?.isDirectory()) return join(target, 'index.ts')
  if (st?.isFile() && rel.endsWith('.ts')) return target
  throw new Error(`条目既非存在的目录也非 .ts 文件: ${rel}`)
}

/** import 模块 → 收集 Routine 子类.
 *
 * ESM export 即 manifest: export 什么收什么(排除 Routine 基类自己与 _ 前缀
 * 私有名). seen 跨条目去重. */
async function collectModuleClasses(modulePath: string,
                                    seen: Set<RoutineClass>): Promise<RoutineClass[]> {
  const mod: any = await import(modulePath)
  const out: RoutineClass[] = []
  for (const [name, val] of Object.entries(mod)) {
    if (typeof val !== 'function' || name.startsWith('_')) continue
    const cls = val as RoutineClass
    // prototype instanceof 自身为 false ---- 天然排除 Routine 基类
    if (!(cls.prototype instanceof Routine)) continue
    if (seen.has(cls)) continue
    seen.add(cls)
    out.push(cls)
  }
  return out
}

/** 条目 → 其暴露的 Routine 类(import + collect). */
async function entryClasses(rel: string, seen: Set<RoutineClass>): Promise<RoutineClass[]> {
  return await collectModuleClasses(entryModulePath(rel), seen)
}

/** yaml 条目 kwargs 覆盖式注入 passive 类的 isPassive(PassiveConfig).
 *
 * 覆盖不叠加: yaml kwargs 是部署配置的完整声明. 非 passive 类忽略
 * (手动 submit 传参, 与 yaml 无关). */
function injectPassiveKwargs(classes: RoutineClass[], kw: Record<string, any>): void {
  if (!kw || !Object.keys(kw).length) return
  for (const cls of classes) {
    if (passiveFlag(cls)) (cls as any).isPassive = new PassiveConfig({ ...kw })
  }
}

function passiveFlag(cls: RoutineClass): boolean {
  const ip = (cls as any).isPassive
  return ip === true || ip instanceof PassiveConfig
}

/** 已解析条目 → import 模块 → 收集 → 注入 kwargs → 注册. */
async function loadEntries(entries: Entry[]): Promise<Routines> {
  const rs = new Routines()
  const seen = new Set<RoutineClass>()
  for (const { rel, kwargs } of entries) {
    const classes = await entryClasses(rel, seen)
    if (!classes.length) {
      log.warning(`${rel}: 未暴露任何 Routine 子类 (空条目, 跳过)`)
      continue
    }
    injectPassiveKwargs(classes, kwargs)
    rs.register(...classes)
  }
  return rs
}

/** 引导 routine: 被自动拉起后按 routines.yaml 注册其余全部 routine.
 *
 * 唯一静态注册的 routine(引导扇区); yaml 不列自己, 元层不进清单.
 * 注册完成 run() 自然返回 ---- 实例 auto 停止. */
export class RoutinesLoader extends Routine {
  static name = 'ts_routines_loader'
  static isPassive = true
  static meta: Record<string, any> = {
    hidden: true,
    description: '引导 routine: 拉起后按 routines.yaml 运行时注册其余全部 routine.',
  }

  async run(kwargs: Payload): Promise<any> {
    let rs: Routines
    try {
      rs = await loadEntries(readYamlEntries())
    } catch (exc: any) {
      log.error(`load routines.yaml failed: ${exc?.message ?? exc}`)
      return
    }
    const hub = this.ctx.hub

    const registered: string[] = []
    const skipped: string[] = []
    for (const cls of rs.getRoutines()) {
      const name = routineNameOf(cls)
      try {
        await hub.registerRoutine(cls)
        registered.push(name)
        log.info(`✅ ${name}`)
      } catch {
        skipped.push(name)
      }
    }
    log.info(`loader: registered ${registered.length} routines, skipped ${skipped.length} (already in kernel): ${skipped.join(', ')}`)
  }
}
