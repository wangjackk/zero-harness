/* 拖拽截图：node scripts/drag-shot.mjs <url> <out.png> <dx> <dy> [waitMs]
   在把手 (.grip) 上按下，按 CDP 的真实鼠标事件拖 (dx, dy)，松开后截图。
   dx / dy 为负 = 往左上拖 = 放大。
   合成 PointerEvent 不行：setPointerCapture 对无效 pointerId 会抛错，
   事件链在第一步就断了 —— 必须走 Input.dispatchMouseEvent。 */
const [, , url, out, dxArg, dyArg, waitArg] = process.argv
const dx = Number(dxArg ?? -90)
const dy = Number(dyArg ?? -90)
const wait = Number(waitArg ?? 4200)
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
  width: 1280, height: 900, deviceScaleFactor: 2, mobile: false
})
await send('Page.navigate', { url })
await new Promise(r => setTimeout(r, wait))

const box = `(() => {
  const g = document.querySelector('.hud .grip')
  const hud = document.querySelector('.hud')
  const st = document.querySelector('.hud .stage')
  if (!g || !hud || !st) return null
  const r = g.getBoundingClientRect()
  return { x: r.x + r.width / 2, y: r.y + r.height / 2,
           w: hud.getBoundingClientRect().width, h: st.getBoundingClientRect().height,
           sc: getComputedStyle(hud).getPropertyValue('--sc').trim() }
})()`
const before = (await send('Runtime.evaluate', { returnByValue: true, expression: box })).result?.value
if (!before) {
  console.log('no grip')
} else {
  const x0 = before.x
  const y0 = before.y
  const at = (k) => ({ x: x0 + dx * k, y: y0 + dy * k })
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: x0, y: y0, button: 'none' })
  await send('Input.dispatchMouseEvent', {
    type: 'mousePressed', x: x0, y: y0, button: 'left', buttons: 1, clickCount: 1
  })
  for (let i = 1; i <= 8; i++) {
    const p = at(i / 8)
    await send('Input.dispatchMouseEvent', {
      type: 'mouseMoved', x: p.x, y: p.y, button: 'left', buttons: 1
    })
    await new Promise(r => setTimeout(r, 16))
  }
  const p = at(1)
  await send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', x: p.x, y: p.y, button: 'left', buttons: 0, clickCount: 1
  })
  await new Promise(r => setTimeout(r, 320))
}

/* 松手后再停到一片叶上：拖大之后字号是否还压在树冠里，一眼就能看出来 */
const leaf = (await send('Runtime.evaluate', {
  returnByValue: true,
  expression: `(() => {
    const ps = [...document.querySelectorAll('svg.tree path.leaf')]
      .filter(p => { const r = p.getBoundingClientRect(); return r.width > 0 && getComputedStyle(p).opacity !== '0' })
    if (!ps.length) return null
    const rows = ps.map(p => { const r = p.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 } })
    rows.sort((a, b) => a.y - b.y)
    return rows[Math.floor(rows.length * 0.35)]
  })()`
})).result?.value
if (leaf) {
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: leaf.x, y: leaf.y, button: 'none' })
  await new Promise(r => setTimeout(r, 420))
}

const after = (await send('Runtime.evaluate', { returnByValue: true, expression: box })).result?.value
const label = await send('Runtime.evaluate', {
  returnByValue: true,
  expression: `[...document.querySelectorAll('svg.tree text.tip')]
    .filter(t => getComputedStyle(t).display !== 'none')
    .map(t => ({ name: t.textContent, px: getComputedStyle(t).fontSize }))`
})
const shot = await send('Page.captureScreenshot', { format: 'png' })
const fs = await import('node:fs/promises')
await fs.writeFile(out, Buffer.from(shot.data, 'base64'))
console.log('before', before, 'after', after, 'label', JSON.stringify(label.result?.value))
await fetch(`${base}/json/close/${t.id}`)
ws.close()
