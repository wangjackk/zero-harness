/* 极简 Python 高亮：单趟分词，避免“替换出来的标签又被下一次替换改写”。 */

const RE =
  /('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")|(#[^\n]*)|(\b\d+(?:\.\d+)?\b)|(\b(?:class|def|async|await|return|try|except|finally|if|elif|else|for|while|in|import|from|with|as|raise|pass|and|or|not|is|lambda|yield)\b)|(\bself\b)|(\b(?:None|True|False)\b)|(\b[A-Z][A-Za-z0-9_]*\b)/g

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

export function hl(line: string): string {
  if (!line) return ''
  let out = ''
  let last = 0
  RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = RE.exec(line))) {
    out += esc(line.slice(last, m.index))
    let cls = 'n'
    if (m[1]) cls = 'q'
    else if (m[2]) cls = 'c'
    else if (m[3]) cls = 'n'
    else if (m[4]) cls = 'k'
    else if (m[5]) cls = 's'
    else if (m[6]) cls = 'k'
    else if (m[7]) cls = 't'
    out += `<i class="${cls}">${esc(m[0])}</i>`
    last = m.index + m[0].length
    if (m[0].length === 0) RE.lastIndex++
  }
  out += esc(line.slice(last))
  return out
}
