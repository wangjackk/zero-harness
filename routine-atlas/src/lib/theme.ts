import { ref, watch } from 'vue'

const KEY = 'atlas-theme'
const dark = ref(true)

function apply(v: boolean) {
  document.documentElement.dataset.theme = v ? 'dark' : 'light'
  document.documentElement.style.colorScheme = v ? 'dark' : 'light'
}

export function useTheme() {
  /* 这本图集的本体是深色仪表盘；系统偏好只在用户手动切过后才生效。 */
  const saved = localStorage.getItem(KEY)
  dark.value = saved ? saved === 'dark' : true
  apply(dark.value)
  watch(dark, v => {
    apply(v)
    localStorage.setItem(KEY, v ? 'dark' : 'light')
  })
  return { dark, toggle: () => (dark.value = !dark.value) }
}
