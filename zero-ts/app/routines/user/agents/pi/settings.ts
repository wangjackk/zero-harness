/** pi/settings/* -- pi 引擎全局配置 (settings.json) 读写.
 *
 * pi 的配置事实源分两处, routine 命名空间与之对齐:
 *   - ~/.pi/agent/settings.json  偏好设置 (SettingsManager, 本文件 pi/settings/*)
 *   - models.yaml (项目根, 经 py 侧 models/* 中立管理; pi 引擎侧由
 *     _core/modelRuntime.ts 运行时注册, 不再读写 models.json)
 * 字段名与 pi Settings 接口一致 (camelCase, 如 defaultModel/defaultProvider),
 * 不做翻译; 只读写 global scope (project scope 依赖具体项目 cwd, 不在此暴露).
 */
import { z } from 'zod'

import { Routine } from 'routine-ts'
import type { Payload } from 'routine-ts'

import { readFile, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

import { SettingsManager, getAgentDir } from '@earendil-works/pi-coding-agent'

import { jsonSchema } from '../../toolSchema'

const PiGetSettingsOutput = z.object({
  settings: z.record(z.string(), z.any())
    .describe('pi settings.json 内容 (global scope, 已过 pi 旧格式迁移)'),
})

export class PiGetSettings extends Routine {
  static name = 'pi/settings/get'

  /** 读 pi 全局 settings.json (经 SettingsManager, 含旧格式迁移与容错). */

  static meta: Record<string, any> = {
    description: (
      'Read pi engine global settings (~/.pi/agent/settings.json). Fields follow '
      + 'the pi Settings schema (camelCase): defaultProvider, defaultModel, '
      + 'defaultThinkingLevel, theme, enabledModels, terminal, ...'
    ),
    output_schema: jsonSchema(PiGetSettingsOutput),
  }

  async run(_kwargs: Payload): Promise<Record<string, any>> {
    const manager = SettingsManager.create(getAgentDir(), getAgentDir())
    return { settings: manager.getGlobalSettings() }
  }
}

// ──────────────────────────────────────────────────────────────────────────────

const PiSetSettingsInput = z.object({
  settings: z.record(z.string(), z.any()).describe(
    'Partial settings patch (pi Settings fields, camelCase), e.g. '
    + '{"defaultProvider": "seed", "defaultModel": "glm-5.3"}. '
    + 'Top-level fields are replaced; nested objects merge per-key (pi semantics).',
  ),
})

const PiSetSettingsOutput = z.object({
  ok: z.boolean().describe('true = written'),
  settings: z.record(z.string(), z.any()).describe('merged settings after write'),
})

export class PiSetSettings extends Routine {
  static name = 'pi/settings/set'

  /** patch 写 pi 全局 settings.json (浅 merge, 嵌套对象按 key merge, 同 pi 持久化语义). */

  static meta: Record<string, any> = {
    input_schema: jsonSchema(PiSetSettingsInput),
    output_schema: jsonSchema(PiSetSettingsOutput),
    description: (
      'Patch pi engine global settings (~/.pi/agent/settings.json). Accepts pi '
      + 'Settings fields (camelCase) and merges them into the file; returns the '
      + 'merged settings.'
    ),
  }

  async run(kwargs: Payload): Promise<Record<string, any>> {
    const patch = (kwargs as Record<string, any>)?.settings
    if (typeof patch !== 'object' || patch === null || Array.isArray(patch)) {
      throw new Error('settings: object required')
    }
    const path = join(getAgentDir(), 'settings.json')
    const current = await readJsonOrEmpty(path)
    const merged = mergePatch(current, patch)
    await writeFile(path, JSON.stringify(merged, null, 2), 'utf-8')
    return { ok: true, settings: merged }
  }
}

// ──────────────────────────────────────────────────────────────────────────────

async function readJsonOrEmpty(path: string): Promise<Record<string, any>> {
  try {
    return JSON.parse(await readFile(path, 'utf-8'))
  } catch (exc: any) {
    if (exc?.code === 'ENOENT') return {}
    throw exc
  }
}

function isPlainObject(v: any): v is Record<string, any> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** pi 持久化语义: 顶层字段覆盖, 嵌套 plain object 按 key merge (数组/标量整体替换). */
function mergePatch(current: any, patch: any): Record<string, any> {
  const out: Record<string, any> = { ...current }
  for (const [key, value] of Object.entries(patch)) {
    out[key] = isPlainObject(value) && isPlainObject(out[key])
      ? mergePatch(out[key], value)
      : value
  }
  return out
}