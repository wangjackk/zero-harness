import { createApp } from 'vue'
import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'
import './styles/main.css'
import App from './App.vue'
import router from './router'
import { reveal } from './lib/reveal'

createApp(App).use(router).directive('reveal', reveal).mount('#app')
