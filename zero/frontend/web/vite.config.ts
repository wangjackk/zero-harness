import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    port: 5173,
    allowedHosts: true,
    proxy: {
      '/ws': {
        target: 'ws://127.0.0.1:7781',
        ws: true,
      },
    },
  },
})
