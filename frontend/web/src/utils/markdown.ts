import { marked } from 'marked'

// Tags allowed to pass through from source into rendered HTML.
// Anything else (e.g. <script>, <iframe>) is neutralized by wrapping it in
// backticks, so assistant/system output cannot inject arbitrary HTML.
const SAFE_TAGS = new Set([
  'br', 'hr', 'p', 'span', 'div', 'strong', 'em', 'code', 'pre',
  'ul', 'ol', 'li', 'a', 'b', 'i', 'u', 'blockquote',
  'table', 'thead', 'tbody', 'tr', 'td', 'th',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
])

function escapeUnsafeTags(content: string): string {
  return content.replace(/<(\/?[A-Za-z_][^<>]*)>/g, (m, inner: string) => {
    const tag = inner.replace(/^\//, '').split(/[\s/>]/)[0].toLowerCase()
    return SAFE_TAGS.has(tag) ? m : `\`${m}\``
  })
}

// Render markdown to an HTML string safe for v-html. Unknown HTML tags in the
// source are escaped first; marked (GFM on by default -> tables, task lists)
// then produces the final HTML. Returns '' for empty input.
//
// breaks: convert single "\n" to <br>. Chat output expects newlines to read as
// line breaks (mirrors white-space: pre-wrap), so the chat bar passes true.
// System prompts leave it false (standard markdown soft-break behaviour).
export function renderMarkdown(
  content: string,
  opts: { breaks?: boolean } = {},
): string {
  if (!content) return ''
  return marked.parse(escapeUnsafeTags(content), { breaks: opts.breaks ?? false }) as string
}
