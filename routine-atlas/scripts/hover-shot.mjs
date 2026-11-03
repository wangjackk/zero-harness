/* 悬停截图：node scripts/hover-shot.mjs <url> <out.png> [waitMs] [expr] [sel]
   先在页面里跑 expr（比如点放大），再定位目标元素的真实屏幕坐标，
   用 CDP 移动鼠标过去（CSS :hover 只能靠真实指针状态触发，派发事件不行），
   等过渡走完再截。sel 默认取一片叶；传 `.hits path` 可验「悬停枝」。
   path 一律走 getPointAtLength 取曲线上的点 —— 取包围盒中心会落空
   （斜枝的 bbox 中心根本不在枝上）。 */
const [, , url, out, waitArg, exprArg, selArg] = process.argv
const wait = Number(waitArg ?? 4200)
const sel = selArg ?? 'svg.tree path.leaf'
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
if (exprArg) {
  await send('Runtime.evaluate', { expression: exprArg, returnByValue: true })
  await new Promise(r => setTimeout(r, 400))
}

/* 取目标元素上的一个真实点：叶的包围盒随生长在变，所以取当前帧可见的那些 */
const probe = await send('Runtime.evaluate', {
  returnByValue: true,
  expression: `(() => {
    const ps = [...document.querySelectorAll(${JSON.stringify(sel)})]
      .filter(p => { const r = p.getBoundingClientRect(); return r.width > 0 || r.height > 0 })
    if (!ps.length) return null
    const rows = ps.map(p => {
      if (p.getTotalLength) {
        const L = p.getTotalLength()
        const pt = p.getPointAtLength(L * 0.55)
        const q = new DOMPoint(pt.x, pt.y).matrixTransform(p.getScreenCTM())
        return { x: q.x, y: q.y, w: Math.round(p.getBoundingClientRect().width) }
      }
      const r = p.getBoundingClientRect()
      return { x: r.x + r.width / 2, y: r.y + r.height / 2, w: Math.round(r.width) }
    })
    rows.sort((a, b) => a.y - b.y)
    return rows[Math.floor(rows.length * 0.35)]
  })()`
})
const at = probe.result?.value
if (!at) {
  console.log('no leaf found')
} else {
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: at.x, y: at.y, button: 'none' })
  await new Promise(r => setTimeout(r, 500))
}

const shot = await send('Page.captureScreenshot', { format: 'png' })
const fs = await import('node:fs/promises')
await fs.writeFile(out, Buffer.from(shot.data, 'base64'))

/* 顺手把「名字出没出来」读回来，别只看图 */
const label = await send('Runtime.evaluate', {
  returnByValue: true,
  expression: `[...document.querySelectorAll('svg.tree text.tip')].filter(t => getComputedStyle(t).display !== 'none').map(t => t.textContent)`
})
console.log('hover at', at, 'labels', JSON.stringify(label.result?.value))
await fetch(`${base}/json/close/${t.id}`)
ws.close()
