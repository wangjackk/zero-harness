/** toolSchema -- 把 routine 类转成 LLM function-calling schema. 移植 tool_schema.py.
 *
 * 用 meta['input_schema'] (JSON Schema dict). 无 input_schema 时退化成空 object.
 */
import { z } from 'zod'

import { routineNameOf, type RoutineClass } from 'routine-ts'

/** zod schema → JSON Schema (py pydantic ``model_json_schema()`` 的 ts 等价物).
 *
 * io:'input' 视角 ---- defaulted 字段不进 required(调用方可省); 去掉 ``$schema``
 * 方言声明噪声(pydantic 也不产出).
 */
export function jsonSchema(s: z.ZodType): Record<string, any> {
  const { $schema: _omit, ...rest } = z.toJSONSchema(s, { io: 'input' })
  return rest as Record<string, any>
}

/** 生成单个工具的 function-calling schema.
 * 返回值可直接放进 Responses API 的 tools=[...] 列表. */
export function toolSchema(cls: RoutineClass): Record<string, any> {
  const meta = (cls as any).meta ?? {}
  let parameters: Record<string, any>
  const inputSchema = meta.input_schema
  if (inputSchema && typeof inputSchema === 'object') {
    parameters = { ...inputSchema }
    // 顶层 title/description 是模型自带的,工具描述单独放外层,避免重复
    delete parameters.title
    delete parameters.description
    stripHiddenFields(parameters)
  } else {
    parameters = { type: 'object', properties: {}, required: [] }
  }
  return {
    type: 'function',
    name: routineNameOf(cls),
    description: meta.description || routineNameOf(cls),
    parameters,
  }
}

/** 批量生成 schema 列表,可按 meta 过滤. */
export function toolSchemas(
  routines: RoutineClass[],
  opts: {
    onlyTools?: boolean
    readonlyOnly?: boolean
    whitelist?: Set<string> | null
  } = {},
): Record<string, any>[] {
  const { onlyTools = true, readonlyOnly = false, whitelist = null } = opts
  const result: Record<string, any>[] = []
  for (const cls of routines) {
    const meta = (cls as any).meta ?? {}
    if (onlyTools && !meta.tool) continue
    if (readonlyOnly && !meta.readonly) continue
    const name = routineNameOf(cls)
    if (whitelist && !whitelist.has(name)) continue
    result.push(toolSchema(cls))
  }
  return result
}

/** 从 JSON Schema properties 移除标记 x-hidden 的字段.
 * 框架注入字段声明: 模型里有定义, 但不出现在给 LLM 的 tool schema 里. */
function stripHiddenFields(parameters: Record<string, any>): void {
  const props = parameters.properties
  if (!props || typeof props !== 'object') return
  const hidden = Object.entries(props)
    .filter(([, spec]) => spec && typeof spec === 'object' && (spec as any)['x-hidden'])
    .map(([name]) => name)
  if (!hidden.length) return
  for (const name of hidden) delete props[name]
  if (Array.isArray(parameters.required)) {
    parameters.required = parameters.required.filter((r: any) => !hidden.includes(r))
  }
}
