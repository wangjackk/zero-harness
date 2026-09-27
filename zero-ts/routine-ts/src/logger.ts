/** routine 日志器(与 z2/logger.py 格式一致的着色输出).

 格式:2025-10-27 15:16:28.080 INFO zt.kernel - message
 整行按级别着色(INFO 绿 / DEBUG 青 / WARNING 黄 / ERROR 红).
 */
const RESET = '\x1b[0m'
const RED = '\x1b[31m'
const YELLOW = '\x1b[33m'
const GREEN = '\x1b[32m'
const CYAN = '\x1b[36m'
const GRAY = '\x1b[90m'

const LEVELS: Record<string, number> = { debug: 10, info: 20, warning: 30, error: 40 }
const COLORS: Record<string, string> = {
  debug: CYAN, info: GREEN, warning: YELLOW, error: RED,
}

export interface Logger {
  debug(msg: string, ...args: any[]): void
  info(msg: string, ...args: any[]): void
  warning(msg: string, ...args: any[]): void
  error(msg: string, ...args: any[]): void
  readonly name: string
}

const registry = new Map<string, { logger: Logger; level: number }>()
let defaultLevel = LEVELS.info

function fmtTime(d: Date): string {
  const p = (n: number, w = 2) => String(n).padStart(w, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`
}

function fmtArgs(args: any[]): string {
  if (!args.length) return ''
  return ' ' + args.map(a => {
    if (a instanceof Error) return a.stack ?? String(a)
    if (typeof a === 'object') {
      try { return JSON.stringify(a) } catch { return String(a) }
    }
    return String(a)
  }).join(' ')
}

/** 从 Error.stack 提取调用方 filename:lineno (跳过 logger.ts 内部帧).
 * 与 py ColoredFormatter 的 record.filename:record.lineno 等价. */
function callerOf(stack: string | undefined): string {
  if (!stack) return ''
  for (const raw of stack.split('\n')) {
    const line = raw.trim()
    if (!line.startsWith('at ')) continue
    if (/[\\/]logger\.ts:/.test(line)) continue
    const m = /\(?([^()\\/:]+):(\d+):\d+\)?\s*$/.exec(line)
    if (!m) continue
    return `${m[1]}:${m[2]}`
  }
  return ''
}

function emit(name: string, level: string, msg: string, args: any[], threshold: number): void {
  if (LEVELS[level] < threshold) return
  const color = COLORS[level] ?? RESET
  const line = `${color}${fmtTime(new Date())} ${level.toUpperCase().padEnd(7)} ` +
    `${name} - ${msg}${fmtArgs(args)}${RESET} ${GRAY}${callerOf(new Error().stack)}${RESET}`
  console.log(line)
}

export function setupLogger(name: string, level?: string): Logger {
  const existing = registry.get(name)
  if (existing) return existing.logger
  // 未显式指定 level 时跟随 defaultLevel(configureRoot 可统一下调)
  const lv = level === undefined ? defaultLevel : (LEVELS[level] ?? defaultLevel)
  const entry = { logger: null as any, level: lv }
  entry.logger = {
    name,
    debug: (m: string, ...a: any[]) => emit(name, 'debug', m, a, entry.level),
    info: (m: string, ...a: any[]) => emit(name, 'info', m, a, entry.level),
    warning: (m: string, ...a: any[]) => emit(name, 'warning', m, a, entry.level),
    error: (m: string, ...a: any[]) => emit(name, 'error', m, a, entry.level),
  }
  registry.set(name, entry)
  return entry.logger
}

export function setLevel(name: string, level: string): void {
  const entry = registry.get(name)
  if (entry) entry.level = LEVELS[level] ?? entry.level
}

/** 统一设置所有已建 logger 与后续默认级别(应用入口调一次). */
export function configureRoot(level: string = 'info'): void {
  defaultLevel = LEVELS[level] ?? defaultLevel
  for (const entry of registry.values()) entry.level = defaultLevel
}
