/** 工具桥: pi ToolDefinition -> 本仓 routine.
 *
 * pi 引擎里的工具全部经 agent.call 走 kernel 路由到 py 侧 routine,
 * TS 侧只做定义与转发 (ZMQ kernel / 文件操作等复用本仓实现, 不在 pi 侧再造).
 */
import type { ToolDefinition } from '@earendil-works/pi-coding-agent'
import { readFile } from 'node:fs/promises'
import { z } from 'zod'

import { jsonSchema } from '../../toolSchema'

import { AGENT_ID_KEY } from '../_core/paths'

const IPYTHON_DESCRIPTION = (
  'Execute Python code in a persistent per-session IPython kernel. '
  + 'Use it for data parsing, JSON/CSV/log analysis, quick calculations, regex experiments, '
  + 'and validating small algorithms. Variables and imports persist across calls in the same session. '
  + 'IPython magics are available (e.g. %timeit, %reset, %cd, !cmd). '
  + 'Print ONLY data — compact and decision-useful, no prose. '
  + 'This tool can read/write files and execute arbitrary Python, so use it carefully.'
)

// schema 用 zod 声明 (对齐 py pydantic 约定), 经 jsonSchema() 转 JSON Schema
const IpythonParams = z.object({
  code: z.string().describe('Python code to execute in the persistent IPython kernel.'),
  timeout: z.number().int().min(1).max(120).optional()
    .describe('Timeout in seconds. Default 120, max 120.'),
  reset: z.boolean().optional()
    .describe('Clear the IPython user namespace before executing this code. Preserves the kernel process.'),
})

/** 工具执行需要的 agent 能力子集 (PiAgent 提供). */
export interface ToolHost {
  agentId: string
  /** signal 触发时 stop 底层 routine (py 侧 stop hook 打断 kernel). */
  call(name: string, kwargs: Record<string, any>, signal?: AbortSignal): Promise<any>
}

const MIME_BY_EXT: Record<string, string> = {
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
  gif: 'image/gif', webp: 'image/webp',
}

/** image content block (绝对路径 | data: URL) -> pi ImageContent {data: base64, mimeType}. */
export async function toPiImage(
  path: string,
): Promise<{ type: 'image'; data: string; mimeType: string }> {
  if (path.startsWith('data:')) {
    const m = /^data:([^;,]+);base64,(.+)$/s.exec(path)
    if (!m) throw new Error(`invalid data url: ${path.slice(0, 32)}`)
    return { type: 'image', data: m[2], mimeType: m[1] }
  }
  const buf = await readFile(path)
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  return { type: 'image', data: buf.toString('base64'), mimeType: MIME_BY_EXT[ext] ?? 'image/png' }
}

export function makeIpthonTool(host: ToolHost): ToolDefinition {
  return {
    name: 'ipython',
    label: 'IPython',
    description: IPYTHON_DESCRIPTION,
    parameters: jsonSchema(IpythonParams) as ToolDefinition['parameters'],
    execute: async (_toolCallId, params, signal) => {
      const kwargs: Record<string, any> = { ...(params as Record<string, any>) }
      kwargs[AGENT_ID_KEY] = host.agentId
      const result = await host.call('ipython', kwargs, signal)
      const res = (result as Record<string, any> | null) ?? {}
      // text + display 捕获的图像附件 (prime attach 机制: plt.show() / display()
      // 的图进 toolResult 的 image block, 模型可直接看到)
      const content: Array<{ type: 'text'; text: string }
        | { type: 'image'; data: string; mimeType: string }> = [
        { type: 'text', text: String(res.for_llm ?? '') },
      ]
      for (const d of (res.displays ?? []) as Array<{ mime: string; data: string }>) {
        content.push({ type: 'image', data: d.data, mimeType: d.mime })
      }
      return { content, details: result }
    },
  }
}
