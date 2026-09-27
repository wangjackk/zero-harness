/** Agent 消息约定: 优先级常量 + from 头包装 + content 数组协议.
 *
 * priority: 决定接收方是否打断当前 react. 高 (>=PRIORITY_HIGH) 立即处理 (打断);
 * 低排队等 idle. user 默认高, agent 默认低.
 *
 * 消息协议 (chat_message req / user_input pubsub, 跨语言 py/ts 一致):
 *
 *     {
 *         "from": "user" | <agent_id>,
 *         "priority": number,                // 可选; 缺省高
 *         "content": [                       // discriminated union 数组
 *             { "type": "text", "text": string },
 *             { "type": "image", "path": string },  // 绝对路径 | data: URL
 *         ],
 *     }
 *
 * content 由入口层 (py 侧 send_message / web_server) 组装: 前端上传返回的
 * /files/<name> 引用在 py 侧 resolve 成绝对路径, ts 消费方无需 web_server
 * 目录知识. 未知 type 立即抛错 (协议违规直接暴露).
 *
 * agent 内部投递形态是 { text, from, images } (与 py 侧 reactor 一致):
 * text 是主体 (wrap_from 包装), images 是 path 列表, 拆解只发生在入口.
 */
export const USER_SOURCE = 'user'

export const PRIORITY_HIGH = 10
export const PRIORITY_LOW = 0

export interface TextBlock { type: 'text'; text: string }
export interface ImageBlock { type: 'image'; path: string }
export type ContentBlock = TextBlock | ImageBlock

/** 给 agent 消息正文加 ``from: <id>`` 头. 用户消息原样返回不加头. */
export function wrapFrom(content: string, fromAgent: string): string {
  if (!fromAgent || fromAgent === USER_SOURCE) return content
  return `from: ${fromAgent}\n${content}`
}

/** 入口层组装: {text, images path} -> content 数组. */
export function buildContent(text: string, images: string[] = []): ContentBlock[] {
  const content: ContentBlock[] = []
  if (text) content.push({ type: 'text', text })
  for (const p of images) content.push({ type: 'image', path: p })
  return content
}

/** 消费层拆解: content 数组 -> [text 主体, image path 列表].
 *
 * 多个 text block 以 \n 连接成单一主体 (LLM 接口是 text 主体 + images 附件). */
export function splitContent(content: unknown): [string, string[]] {
  const texts: string[] = []
  const images: string[] = []
  for (const block of (content ?? []) as any[]) {
    if (typeof block !== 'object' || block === null) {
      throw new Error(`content block must be object, got ${JSON.stringify(block)}`)
    }
    if (block.type === 'text') texts.push(String(block.text ?? ''))
    else if (block.type === 'image') images.push(String(block.path ?? ''))
    else throw new Error(`unsupported content type: ${JSON.stringify(block.type)}`)
  }
  return [texts.filter(t => t).join('\n'), images]
}