import { createApp } from 'vue'

/* 单入口分流: /flow (或 #/flow) → flow 独立界面, 其余 → 主应用.
   两路都动态 import: 路由只加载自己的代码与样式 (主应用全局 CSS
   #app{flex-direction:column} 不再污染 flow 布局). */
const p = location.pathname.replace(/\/+$/, '').replace(/\.html$/, '')
if (p.endsWith('/flow') || location.hash.startsWith('#/flow')) {
  import('./flow/main').then(m => m.mount())
} else {
  import('./App.vue').then(({ default: App }) => {
    createApp(App).mount('#app')
  })
}
