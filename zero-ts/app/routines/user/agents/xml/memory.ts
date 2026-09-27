/** XmlAgent(TS) 记忆 ---- bun:sqlite, py xml_agent/memory.py 的最小同构移植.
 *
 * 两张表: messages(对话历史) + agents(agent 注册表). 无 OV / 无 summary /
 * 无 response_id 链 ---- prompt caching 走 pi-ai 的 sessionId(prompt_cache_key),
 * 全量发历史, 不需要增量锚点.
 *
 * 写同步执行(bun:sqlite 快, 单 agent 写频率低), 不搞 py 的后台写线程.
 * DB: <harness 根>/runtime/xml_agent_ts/memory.db, 与 py 版(runtime/xml_agent/)
 * 隔离, 重启保留.
 */
import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { Database } from 'bun:sqlite'

// memory.ts 在 zero-ts/app/routines/user/agents/xml/ 下, 6 级 .. = harness 根
const DB_PATH = resolve(
  import.meta.dir, '..', '..', '..', '..', '..', '..',
  'runtime', 'xml_agent_ts', 'memory.db',
)

function nowIso(): string {
  return new Date().toISOString()
}

/** history 消息(load_history 出口, 喂 LLM / 投影用). */
export interface HistoryMsg {
  message_id: string
  role: string
  content: string
  interrupted: boolean
  feedback: any[] | null
  images: string[] | null
}

/** agents 表行. */
export interface XmlAgentRow {
  agent_id: string
  session_id: string | null
  model: string | null
  protocol: string | null
  title: string | null
  created_at: string
  updated_at: string
}

export class Memory {
  private _db: Database
  readonly path: string

  constructor(dbPath?: string) {
    this.path = dbPath ?? DB_PATH
    mkdirSync(join(this.path, '..'), { recursive: true })
    this._db = new Database(this.path)
    this._db.exec('PRAGMA journal_mode=WAL')
    this._db.exec('PRAGMA synchronous=NORMAL')
    this._initSchema()
  }

  close(): void { this._db.close() }

  private _initSchema(): void {
    this._db.exec(`
      CREATE TABLE IF NOT EXISTS messages (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          message_id  TEXT    NOT NULL,
          session_id  TEXT    NOT NULL,
          role        TEXT    NOT NULL,
          content     TEXT    NOT NULL DEFAULT '',
          interrupted INTEGER NOT NULL DEFAULT 0,
          ts          TEXT    NOT NULL,
          feedback    TEXT,
          usage       TEXT,
          images      TEXT
      )`)
    this._db.exec(
      'CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)')
    this._db.exec(`
      CREATE TABLE IF NOT EXISTS agents (
          agent_id   TEXT PRIMARY KEY,
          session_id TEXT,
          model      TEXT,
          protocol   TEXT,
          title      TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
      )`)
  }

  addMessage(role: string, content: string, opts: {
    agentId: string
    messageId: string
    sessionId: string
    interrupted?: boolean
    feedback?: any[] | null
    usage?: Record<string, any> | null
    images?: string[] | null
  }): void {
    const ts = nowIso()
    const fb = opts.feedback?.length ? JSON.stringify(opts.feedback) : null
    const us = opts.usage ? JSON.stringify(opts.usage) : null
    const im = opts.images?.length ? JSON.stringify(opts.images) : null
    this._db.query(
      'INSERT INTO messages(message_id, session_id, role, content, '
      + 'interrupted, ts, feedback, usage, images) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)',
    ).run(opts.messageId, opts.sessionId, role, content || '',
      opts.interrupted ? 1 : 0, ts, fb, us, im)
    this._db.query('UPDATE agents SET updated_at = ? WHERE agent_id = ?')
      .run(ts, opts.agentId)
    // 首条真实 user 消息提为 agent 标题 (对齐 py)
    if (role === 'user' && !content.startsWith('[feedback]')) {
      const row = this._db.query(
        'SELECT title FROM agents WHERE agent_id = ?').get(opts.agentId) as { title: string | null } | null
      if (row !== null && !row.title) {
        const title = (content || '').trim().slice(0, 60)
        if (title) {
          this._db.query('UPDATE agents SET title = ? WHERE agent_id = ?')
            .run(title, opts.agentId)
        }
      }
    }
  }

  loadHistory(sessionId: string): HistoryMsg[] {
    const rows = this._db.query(
      'SELECT message_id, role, content, interrupted, feedback, images '
      + 'FROM messages WHERE session_id = ? ORDER BY id ASC',
    ).all(sessionId) as any[]
    return rows.map(r => ({
      message_id: String(r.message_id),
      role: String(r.role),
      content: String(r.content ?? ''),
      interrupted: Boolean(r.interrupted),
      feedback: parseJsonArray(r.feedback),
      images: parseJsonArray(r.images),
    }))
  }

  /** 前端聊天消息格式 (get_history / session_changed 用, 对齐 py load_messages). */
  loadMessages(sessionId: string): Record<string, any>[] {
    const rows = this._db.query(
      'SELECT message_id, role, content, feedback, images '
      + 'FROM messages WHERE session_id = ? ORDER BY id ASC',
    ).all(sessionId) as any[]
    const out: Record<string, any>[] = []
    for (const r of rows) {
      const mid = String(r.message_id)
      const content = String(r.content ?? '')
      if (r.role === 'user') {
        if (content.startsWith('[feedback]')) continue
        const msg: Record<string, any> = {
          id: `hist-user-${mid}`, role: 'user', text: content, final: true,
        }
        const images = parseJsonArray(r.images)
        if (images?.length) msg.images = images
        out.push(msg)
      } else if (r.role === 'assistant') {
        out.push({ id: `hist-asst-${mid}`, role: 'assistant', text: content, final: true })
        const fb = parseJsonArray(r.feedback)
        if (fb?.length) {
          const results: Record<string, any>[] = []
          for (const item of fb) {
            const name = String(item?.name ?? '').trim()
            const value = String(item?.result ?? '').trim()
            if (!name || !value) continue
            const entry: Record<string, any> = { name, status: 'done' }
            if (item?.is_error) entry.error = { msg: value }
            else entry.result = value
            results.push(entry)
          }
          if (results.length) {
            out.push({ id: `hist-tool-${mid}`, role: 'tool', text: '', results, final: true })
          }
        }
      }
    }
    return out
  }

  /** 最后一条带 usage 的 assistant 消息的 token 用量 (resume 回填前端圆环). */
  getLastUsage(sessionId: string): Record<string, any> | null {
    const row = this._db.query(
      "SELECT usage FROM messages WHERE session_id = ? AND role = 'assistant' "
      + "AND usage IS NOT NULL AND usage != '' ORDER BY id DESC LIMIT 1",
    ).get(sessionId) as { usage: string } | null
    if (row === null) return null
    try {
      const v = JSON.parse(row.usage)
      return (v && typeof v === 'object') ? v : null
    } catch { return null }
  }

  // ── agents 表 ──

  registerAgent(agentId: string, opts: {
    sessionId?: string | null
    model?: string | null
    protocol?: string | null
  }): void {
    const ts = nowIso()
    this._db.query(
      'INSERT OR IGNORE INTO agents(agent_id, session_id, model, protocol, created_at, updated_at) '
      + 'VALUES(?, ?, ?, ?, ?, ?)',
    ).run(agentId, opts.sessionId ?? null, opts.model ?? null, opts.protocol ?? null, ts, ts)
    this._db.query(
      'UPDATE agents SET model = ?, protocol = ?, updated_at = ? WHERE agent_id = ?',
    ).run(opts.model ?? null, opts.protocol ?? null, ts, agentId)
  }

  getAgent(agentId: string): XmlAgentRow | null {
    return (this._db.query(
      'SELECT agent_id, session_id, model, protocol, title, created_at, updated_at '
      + 'FROM agents WHERE agent_id = ?',
    ).get(agentId) as XmlAgentRow) ?? null
  }

  listAgents(): XmlAgentRow[] {
    return this._db.query(
      'SELECT agent_id, session_id, model, protocol, title, created_at, updated_at '
      + 'FROM agents ORDER BY updated_at DESC',
    ).all() as XmlAgentRow[]
  }

  /** 删除 agent: agents 行 + 该 session 的所有 messages. 不可恢复. */
  deleteAgent(agentId: string): void {
    const row = this.getAgent(agentId)
    if (row?.session_id) {
      this._db.query('DELETE FROM messages WHERE session_id = ?').run(row.session_id)
    }
    this._db.query('DELETE FROM agents WHERE agent_id = ?').run(agentId)
  }
}

function parseJsonArray(raw: any): any[] | null {
  if (!raw) return null
  try {
    const v = JSON.parse(String(raw))
    return Array.isArray(v) ? v : null
  } catch { return null }
}

// 模块级单例 (多 agent 实例共享同一个 DB 文件)
let _instance: Memory | null = null

export function getMemory(): Memory {
  if (_instance === null) _instance = new Memory()
  return _instance
}
