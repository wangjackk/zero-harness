/* Flow 独立界面入口 (Vue 3, 无 UI 库, 与主应用零共享) */
import { createApp } from 'vue'
import App from './App.vue'
import './style.css'

export function mount(): void {
  createApp(App).mount('#app')
}
