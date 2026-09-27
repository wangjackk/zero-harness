/** Store -- sqlite backend for agent records. 原 _core/store.ts.
 *
 * agent 会话消息/replay/compaction 随自研 agent 体系下线一并移除:
 * pi 引擎的会话持久化由 pi SessionManager 接管 (workspace/pi-sessions/*.jsonl),
 * 本 store 只保留 agent 注册表 (agents 表): agent_id / session_id / model /
 * preset / title / created_at / updated_at.
 *
 * Centralized db: ~/.zero/sessions.db (override: ZERO_SESSION_DB /
 * ZERO_SESSION_DIR env).
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { Database } from 'bun:sqlite'

const CENTRAL_DB_DIR = process.env.ZERO_SESSION_DIR ?? join(homedir(), '.zero')

export interface AgentRow {
  agent_id: string
  session_id: string | null
  model: string | null
  protocol: string | null
  preset: string | null
  title: string | null
  created_at: string
  updated_at: string
}

export class Store {
  private _db: Database
  readonly path: string

  constructor(dbPath?: string) {
    this.path = dbPath
      ?? process.env.ZERO_SESSION_DB
      ?? join(CENTRAL_DB_DIR, 'sessions.db')
    mkdirSync(join(this.path, '..'), { recursive: true })
    this._db = new Database(this.path)
    this._db.exec('PRAGMA journal_mode=WAL')
    this._db.exec('PRAGMA synchronous=NORMAL')
    this._initSchema()
  }

  close(): void { this._db.close() }

  private _initSchema(): void {
    this._db.exec(`
      CREATE TABLE IF NOT EXISTS agents (
          agent_id    TEXT PRIMARY KEY,
          session_id  TEXT,
          model       TEXT,
          title       TEXT,
          created_at  TEXT    NOT NULL,
          updated_at  TEXT    NOT NULL
      )`)
    // migration: 已有 DB 的 agents 表补 session_id / preset / protocol 列
    for (const col of ['session_id', 'preset', 'protocol']) {
      try {
        this._db.exec(`ALTER TABLE agents ADD COLUMN ${col} TEXT`)
      } catch { /* 列已存在 */ }
    }
  }

  // ------------------------------------------------------------------
  // agents
  // ------------------------------------------------------------------

  /** upsert 一个 agents 行 (存元数据 + session_id).
   * session_id 仅在 INSERT 时写入, UPDATE 不覆盖已有值. */
  registerAgent(agentId: string, opts: {
    sessionId?: string | null
    model?: string | null
    protocol?: string | null
    preset?: string | null
  } = {}): void {
    const now = nowIso()
    this._db.query(
      'INSERT OR IGNORE INTO agents(agent_id, session_id, model, protocol, preset, created_at, updated_at) '
      + 'VALUES(?, ?, ?, ?, ?, ?, ?)',
    ).run(agentId, opts.sessionId ?? null, opts.model ?? null, opts.protocol ?? null, opts.preset ?? null, now, now)
    this._db.query(
      'UPDATE agents SET model = ?, protocol = ?, preset = ?, updated_at = ? WHERE agent_id = ?',
    ).run(opts.model ?? null, opts.protocol ?? null, opts.preset ?? null, now, agentId)
  }

  /** all agent rows, newest-updated first (返回元数据 + session_id + preset). */
  listAgents(): AgentRow[] {
    return this._db.query(
      'SELECT agent_id, session_id, model, protocol, preset, title, created_at, updated_at '
      + 'FROM agents ORDER BY updated_at DESC',
    ).all() as AgentRow[]
  }

  getAgent(agentId: string): AgentRow | null {
    return (this._db.query(
      'SELECT agent_id, session_id, model, protocol, preset, title, created_at, updated_at '
      + 'FROM agents WHERE agent_id = ?',
    ).get(agentId) as AgentRow) ?? null
  }

  /** 删除 agent 记录. 不可恢复. */
  deleteAgent(agentId: string): void {
    this._db.query('DELETE FROM agents WHERE agent_id = ?').run(agentId)
  }
}

// ----------------------------------------------------------------------
// module-level single store instance (centralized db).
// ----------------------------------------------------------------------
let _store: Store | null = null
let _testStore: Store | null = null

/** return the singleton Store (centralized db at ~/.zero/sessions.db). */
export function getStore(): Store {
  if (_testStore) return _testStore
  if (!_store) _store = new Store()
  return _store
}

/** inject a temp store for tests (bypasses the singleton). */
export function setTestStore(store: Store | null): void {
  if (_testStore) {
    try { _testStore.close() } catch { /* ignore */ }
  }
  _testStore = store
}

function nowIso(): string {
  return new Date().toISOString()
}
