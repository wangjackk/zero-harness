/* 在页面里跑：找出「可见枝段的根部浮在空中」的枝。
   判据：该枝可见段的起点，离任何其它枝的可见段都超过 d 像素。 */
(() => {
  const d = 2
  const mask = document.querySelector('mask#life-grow')
  if (!mask) return 'no mask'
  const els = [...mask.querySelectorAll('path')]
  const info = els.map((el, i) => {
    const L = el.getTotalLength()
    const off = parseFloat(el.getAttribute('stroke-dashoffset') || '1')
    const vis = Math.max(0, 1 - off) * L
    const w = parseFloat(el.getAttribute('stroke-width') || '1')
    return { i, L, vis, w, base: el.getPointAtLength(0), el }
  })
  const out = []
  for (const a of info) {
    if (a.vis < 0.5) continue
    let best = 1e9
    let who = -1
    for (const b of info) {
      if (b.i === a.i || b.vis < 0.5) continue
      const n = Math.max(2, Math.ceil(b.vis / 5))
      for (let k = 0; k <= n; k++) {
        const q = b.el.getPointAtLength((b.vis * k) / n)
        const dd = Math.hypot(q.x - a.base.x, q.y - a.base.y)
        if (dd < best) {
          best = dd
          who = b.i
        }
      }
    }
    out.push({
      i: a.i,
      vis: +a.vis.toFixed(0),
      w: +a.w.toFixed(1),
      near: +best.toFixed(1),
      who,
      base: [Math.round(a.base.x), Math.round(a.base.y)]
    })
  }
  out.sort((x, y) => y.near - x.near)
  return JSON.stringify({ at: location.hash, visible: out.length, floating: out.filter(o => o.near > d), worst: out.slice(0, 5) })
})()
