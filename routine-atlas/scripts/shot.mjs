/* 无头截图：node scripts/shot.mjs <url> <out.png> [waitMs] [evalBeforeShot]
   走 CDP，等页面动画推进到指定时刻再截；第四个参数是截图前在页面里跑的表达式。 */
const [, , url, out, waitArg, evalArg] = process.argv
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
if (evalArg) {
  await send('Runtime.evaluate', { expression: evalArg, returnByValue: true })
  await new Promise(r => setTimeout(r, 120))
}
const shot = await send('Page.captureScreenshot', { format: 'png' })
const fs = await import('node:fs/promises')
await fs.writeFile(out, Buffer.from(shot.data, 'base64'))
await fetch(`${base}/json/close/${t.id}`)
ws.close()
console.log('ok', out)
