import type { Directive } from 'vue'

/* v-reveal —— 进视口后抬起。值可以是延迟毫秒数；只触发一次。 */
export const reveal: Directive<HTMLElement, number | undefined> = {
  mounted(el, binding) {
    el.classList.add('reveal')
    if (binding.value) el.style.setProperty('--d', `${binding.value}ms`)
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      el.classList.add('is-in')
      return
    }
    const io = new IntersectionObserver(
      entries => {
        for (const e of entries) {
          if (e.isIntersecting) {
            el.classList.add('is-in')
            io.disconnect()
          }
        }
      },
      { threshold: 0.12, rootMargin: '0px 0px -8% 0px' }
    )
    io.observe(el)
    ;(el as HTMLElement & { _io?: IntersectionObserver })._io = io
  },
  unmounted(el) {
    ;(el as HTMLElement & { _io?: IntersectionObserver })._io?.disconnect()
  }
}
