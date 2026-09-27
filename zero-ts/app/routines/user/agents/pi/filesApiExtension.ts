/** Files API 扩展: 内联 base64 图片 -> DS Files API file_id 引用.
 *
 * deepseek 走 openai-completions (无服务端会话状态), 每个请求重发全量历史,
 * base64 图随轮数线性重传. 本扩展挂在 pi 的 before_provider_request 钩子上:
 * 发请求前把 user 消息里的 data: URL 图片上传 Files API (一次, 内容寻址缓存),
 * 之后所有轮次只传 file_id 字符串. 上传失败时保留内联, 不阻断请求.
 */
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

import type { ExtensionFactory } from '@earendil-works/pi-coding-agent'
import { setupLogger } from 'routine-ts/logger'

const log = setupLogger('pi.filesApi')

const DATA_URL_RE = /^data:(image\/[a-z+.-]+);base64,([A-Za-z0-9+/=]+)$/
const EXPIRES_AFTER_SECONDS = 30 * 24 * 3600 // 上传时申请的有效期 (自动过期, 不占配额)

interface CacheEntry {
  file_id: string
  /** 服务端返回的过期时刻 (unix 秒); null = 永久. */
  expires_at: number | null
}

/** 请求载荷里的 user content part (openai-completions 转换产物). */
interface ContentPart {
  type: string
  image_url?: { url?: string }
  file_id?: string
}

export function makeFilesApiExtension(cachePath: string): ExtensionFactory {
  /** key: `<apiKey指纹>:<内容sha256>` -> entry. */
  let cache: Record<string, CacheEntry> | null = null
  let dirty = false
  /** 同图并发去重 (一次请求里/并发请求间). */
  const inflight = new Map<string, Promise<string | null>>()

  const load = (): Record<string, CacheEntry> => {
    if (cache) return cache
    try {
      cache = JSON.parse(readFileSync(cachePath, 'utf8')) as Record<string, CacheEntry>
    } catch {
      cache = {}
    }
    return cache
  }

  const persist = (): void => {
    if (!dirty || !cache) return
    dirty = false
    mkdirSync(dirname(cachePath), { recursive: true })
    writeFileSync(cachePath, JSON.stringify(cache), 'utf8')
  }

  async function upload(
    baseUrl: string, apiKey: string, mime: string, bytes: Buffer,
  ): Promise<CacheEntry> {
    const ext = mime.split('/')[1]?.replace('jpeg', 'jpg') ?? 'png'
    const form = new FormData()
    form.append('purpose', 'user_data')
    // 30 天有效期: 自动过期不占配额; 过期后缓存键失效自动重传
    form.append('expires_after[anchor]', 'created_at')
    form.append('expires_after[seconds]', String(EXPIRES_AFTER_SECONDS))
    form.append('file', new Blob([bytes], { type: mime }), `image.${ext}`)
    const res = await fetch(`${baseUrl}/files`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${apiKey}` },
      body: form,
    })
    if (!res.ok) {
      throw new Error(`files api upload failed: ${res.status} ${await res.text()}`)
    }
    const data = await res.json() as { id?: string; expires_at?: number }
    if (!data.id) throw new Error('files api upload: missing id')
    return { file_id: data.id, expires_at: data.expires_at ?? null }
  }

  /** 缓存命中或上传; 返回 file_id, 失败返回 null (调用方保留内联). */
  const resolve = (
    cacheKey: string, baseUrl: string, apiKey: string, mime: string, bytes: Buffer,
  ): Promise<string | null> => {
    const pending = inflight.get(cacheKey)
    if (pending) return pending
    const task = (async () => {
      const entry = load()[cacheKey]
      const now = Date.now() / 1000
      if (entry && (entry.expires_at === null || entry.expires_at > now + 60)) {
        log.debug(`cache hit: ${cacheKey.slice(0, 28)} -> ${entry.file_id}`)
        return entry.file_id
      }
      const fresh = await upload(baseUrl, apiKey, mime, bytes)
      load()[cacheKey] = fresh
      dirty = true
      log.info(`uploaded: ${cacheKey.slice(0, 28)} -> ${fresh.file_id} (expires_at=${fresh.expires_at ?? 'null'})`)
      return fresh.file_id
    })().catch((exc: any) => {
      log.error(`resolve failed: ${exc?.message ?? exc}`)
      return null
    }).finally(() => { inflight.delete(cacheKey) })
    inflight.set(cacheKey, task)
    return task
  }

  return (pi) => {
    log.info('extension loaded, before_provider_request handler registering')
    pi.on('before_provider_request', async (event, ctx) => {
      try {
        log.debug('before_provider_request fired')
        const payload = event.payload
        if (!payload || typeof payload !== 'object') return undefined
        const messages = (payload as { messages?: unknown }).messages
        if (!Array.isArray(messages)) return undefined

        // Files API 是 DS 专属扩展, 只在 deepseek 端点启用
        const model = ctx.model
        const baseUrl = (model?.baseUrl ?? '').replace(/\/+$/, '')
        if (!baseUrl.includes('api.deepseek.com')) {
          log.debug(`skip: model=${model ? model.provider + '/' + model.id : 'none'} baseUrl=${baseUrl || '(empty)'}`)
          return undefined
        }
        const auth = await ctx.modelRegistry.getApiKeyAndHeaders(model!)
        if (!auth.ok || !auth.apiKey) {
          log.warning(`skip: auth not ready (${auth.ok ? 'no apiKey' : auth.error})`)
          return undefined
        }
        // file_id 归属 API key: 指纹入缓存键, 换 key 自动失效重传
        const keyFp = createHash('sha256').update(auth.apiKey).digest('hex').slice(0, 16)

        let replaced = 0
        for (const msg of messages) {
          if (!msg || typeof msg !== 'object') continue
          const { role, content } = msg as { role?: string; content?: unknown }
          if (role !== 'user' || !Array.isArray(content)) continue
          for (let i = 0; i < content.length; i++) {
            const part = content[i] as ContentPart | null
            if (part?.type !== 'image_url') continue
            const m = DATA_URL_RE.exec(String(part.image_url?.url ?? ''))
            if (!m) continue
            const bytes = Buffer.from(m[2], 'base64')
            const sha = createHash('sha256').update(bytes).digest('hex')
            const cacheKey = `${keyFp}:${sha}`
            const fileId = await resolve(cacheKey, baseUrl, auth.apiKey, m[1], bytes)
            if (fileId) {
              content[i] = { type: 'file', file_id: fileId }
              replaced += 1
              // 16 图会话每轮 provider 请求都重写, per-image 刷屏降到 debug
              log.debug(`image -> file_id=${fileId} (${bytes.length} bytes, sha=${sha.slice(0, 12)})`)
            } else {
              log.warning(`resolve failed, keeping inline image (sha=${sha.slice(0, 12)})`)
            }
          }
        }
        persist()
        if (replaced) {
          log.info(`payload rewritten: ${replaced} image(s) -> file_id`)
        }
        return payload as unknown
      } catch (exc: any) {
        log.error(`handler failed: ${exc?.stack ?? exc}`)
        return undefined
      }
    })
  }
}
