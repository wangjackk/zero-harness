import { ref, onUnmounted } from 'vue'

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

export function useWS(url: string) {
  const connected = ref(false)
  const handlers = new Map<string, Set<MessageHandler>>()
  const binaryHandlers = new Set<BinaryHandler>()

  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let destroyed = false

  function connect() {
    if (destroyed) return
    ws = new WebSocket(url)

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

  function sendInput(text: string): void {
    send({ type: 'user_input', text })
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

  connect()
  onUnmounted(destroy)

  return { connected, on, onBinary, send, request, sendInput, sendInterrupt }
}
