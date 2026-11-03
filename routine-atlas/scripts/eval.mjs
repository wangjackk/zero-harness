/* 无头求值：node scripts/eval.mjs <url> <expr> [waitMs] —— 打印表达式在页面里的返回值
   用来量几何（枝的包围盒、遮罩宽度等），比截图数像素准。 */
const [, , url, expr, waitArg] = process.argv
const wait = Number(waitArg ?? 2500)
const base = 'http://127.0.0.1:9333'

const t = await (await fetch(`${base}/json/new?about:blank`, { method: 'PUT' })).json()
const ws = new WebSocket(t.webSocketDebuggerUrl)
let id = 0
const pending = new Map()

ws.addEventListener('message', e => {
  const m = JSON.parse(e.data)
  if (m.id && pending.has(m.id)) {
    pending.get(m.id)(m.result)
    pending.delete(m.id)
  }
})
await new Promise(r => ws.addEventListener('open', r))
const send = (method, params = {}) =>
  new Promise(res => {
    const n = ++id
    pending.set(n, res)
    ws.send(JSON.stringify({ id: n, method, params }))
  })

await send('Page.enable')
await send('Emulation.setDeviceMetricsOverride', {
  width: 1280,
  height: 900,
  deviceScaleFactor: 2,
  mobile: false
})
await send('Page.navigate', { url })
await new Promise(r => setTimeout(r, wait))
const r = await send('Runtime.evaluate', {
  expression: expr,
  returnByValue: true,
  awaitPromise: true
})
console.log(JSON.stringify(r.result?.value ?? r, null, 2))
await fetch(`${base}/json/close/${t.id}`)
ws.close()
