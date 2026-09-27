import { ref, unref, onUnmounted, type Ref } from 'vue'

const RECONNECT_DELAY = 2000

export type WsMessage = Record<string, unknown> & { type: string }
export type MessageHandler = (msg: WsMessage) => void
export type Unsubscribe = () => void

interface PendingRequest {
  resolve: (msg: WsMessage) => void
  reject: (err: Error) => void
  timer: ReturnType<typeof setTimeout> | undefined
}

export type BinaryHandler = (data: ArrayBuffer) => void

export function useWS(url: string | Ref<string>) {
  const connected = ref(false)
  const handlers = new Map<string, Set<MessageHandler>>()
  const binaryHandlers = new Set<BinaryHandler>()

  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let destroyed = false

  function connect() {
    if (destroyed) return
    ws = new WebSocket(unref(url))

    ws.onopen = () => {
      connected.value = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
    }

    ws.onclose = () => {
      connected.value = false
      ws = null
      if (!destroyed) {
        reconnectTimer = setTimeout(connect, RECONNECT_DELAY)
      }
    }

    ws.onerror = () => {
      ws?.close()
    }

    ws.binaryType = 'arraybuffer'
    ws.onmessage = (event: MessageEvent) => {
      if (event.data instanceof ArrayBuffer) {
        binaryHandlers.forEach(fn => fn(event.data as ArrayBuffer))
        return
      }
      let msg: WsMessage
      try {
        msg = JSON.parse(event.data as string)
      } catch {
        return
      }
      const type = msg?.type
      if (!type) return
      handlers.get(type)?.forEach(fn => fn(msg))
      handlers.get('*')?.forEach(fn => fn(msg))
    }
  }

  function onBinary(fn: BinaryHandler): Unsubscribe {
    binaryHandlers.add(fn)
    return () => binaryHandlers.delete(fn)
  }

  function on(type: string, fn: MessageHandler): Unsubscribe {
    if (!handlers.has(type)) handlers.set(type, new Set())
    handlers.get(type)!.add(fn)
    return () => handlers.get(type)?.delete(fn)
  }

  function send(data: Record<string, unknown>): void {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data))
    }
  }

  function sendInterrupt(): void {
    send({ type: 'interrupt' })
  }

  // 请求-响应:发送消息并等待带相同 id 的回包
  let _reqId = 0
  const _pending = new Map<string, PendingRequest>()

  // timeoutMs <= 0 表示不超时----用于时长无上界的请求(内部可能
  // 等人工审批 300s 甚至多轮 retry,任何固定超时都是错的).
  function request(data: Record<string, unknown>, timeoutMs = 30000): Promise<WsMessage> {
    const id = String(++_reqId)
    return new Promise<WsMessage>((resolve, reject) => {
      const timer = timeoutMs > 0
        ? setTimeout(() => {
            _pending.delete(id)
            reject(new Error('request timeout'))
          }, timeoutMs)
        : undefined
      _pending.set(id, { resolve, reject, timer })
      send({ ...data, id })
    })
  }

  // 所有带 id 的回包自动 resolve pending
  on('*', (msg) => {
    const id = msg?.id
    if (!id || typeof id !== 'string') return
    const pending = _pending.get(id)
    if (!pending) return
    _pending.delete(id)
    clearTimeout(pending.timer)
    pending.resolve(msg)
  })

  function destroy() {
    destroyed = true
    if (reconnectTimer) clearTimeout(reconnectTimer)
    ws?.close()
  }

  /** 立即断开并按当前 url 重连 (切换后端用, 不吃 2s 重连延迟). */
  function reconnect() {
    if (destroyed) return
    if (reconnectTimer) clearTimeout(reconnectTimer)
    if (ws) {
      // 摘掉旧 handler: 旧连接 close 不得再触发自动重连 (会覆盖新连接)
      ws.onclose = null
      ws.onmessage = null
      ws.onerror = null
      ws.close()
      ws = null
    }
    connected.value = false
    connect()
  }

  connect()
  onUnmounted(destroy)

  /** 调用 routine (与 HTTP /run_routine 同语义): 以 user agent 身份.
   *
   * timeoutMs <= 0 不超时 ---- 等用户交互 (ui_request) 的 routine
   * 可能几分钟, 任何固定超时都会误杀. 返回
   * result 内容 (HTTP 版 body), 异常 (transport/不存在) 时 reject.
   */
  async function runRoutine(name: string, kwargs: Record<string, unknown>,
                            timeoutMs = 0): Promise<any> {
    const msg = await request({ type: 'run_routine', name, kwargs }, timeoutMs)
    if (msg.error) throw new Error(String(msg.error))
    return msg.result
  }

  return { connected, on, onBinary, send, request, runRoutine, sendInterrupt, reconnect }
}
