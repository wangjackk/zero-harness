/** XML 工具调用解析 ---- py xml 体系(xml_parser + body_shell)的最小 TS 内嵌版.
 *
 * py 侧 LLM 流式输出边流边解析派发工具子; TS 最小同构: 流完拿全文解析,
 * 工具按出现顺序串行执行. 支持:
 *   - 自闭合:  <name/> 或 <name a="b" n="1"/>
 *   - 开闭对:  <name a="b">body</name> (body 作为 body 参数)
 * 未闭合的开标签按属性调用兜底 (attrs 已完整). 属性值先试 JSON.parse
 * (数字/布尔还原), 失败回退字符串.
 */

export interface XmlToolCall {
  name: string
  kwargs: Record<string, any>
}

const TAG_RE = /<([A-Za-z_][A-Za-z0-9_.-]*)((?:\s+[A-Za-z_][A-Za-z0-9_.-]+="[^"]*")*)\s*(\/?)>/
const ATTR_RE = /([A-Za-z_][A-Za-z0-9_.-]+)="([^"]*)"/g

export function parseXmlToolCalls(text: string): XmlToolCall[] {
  const calls: XmlToolCall[] = []
  let idx = 0
  while (idx < text.length) {
    const m = TAG_RE.exec(text.slice(idx))
    if (m === null) break
    const [full, name, attrs, selfClose] = m
    const start = idx + m.index
    let kwargs = parseAttrs(attrs)
    if (selfClose === '/') {
      calls.push({ name, kwargs })
      idx = start + full.length
    } else {
      const closeTag = `</${name}>`
      const closeIdx = text.indexOf(closeTag, start + full.length)
      if (closeIdx === -1) {
        // 未闭合: 属性当调用兜底, 继续扫描开标签之后
        calls.push({ name, kwargs })
        idx = start + full.length
      } else {
        const body = text.slice(start + full.length, closeIdx).trim()
        if (body) kwargs = { ...kwargs, body }
        calls.push({ name, kwargs })
        idx = closeIdx + closeTag.length
      }
    }
  }
  return calls
}

function parseAttrs(attrs: string): Record<string, any> {
  const out: Record<string, any> = {}
  if (!attrs) return out
  for (const m of attrs.matchAll(ATTR_RE)) {
    out[m[1]] = coerceAttrValue(m[2])
  }
  return out
}

function coerceAttrValue(raw: string): any {
  try {
    const v = JSON.parse(raw)
    if (typeof v === 'number' || typeof v === 'boolean') return v
  } catch { /* 回退字符串 */ }
  return raw
}
