import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    // Windows 动态保留端口区间(Hyper-V/WinNAT, 每次重启重新随机)常盖住
    // 5173/5174 一带 → 固定用区间外端口; 若某次开机再被圈, 换一个即可.
    port: 5250,
    allowedHosts: true,
    proxy: {
      '/ws': {
        // zero2 的 web_server 跑在 7781.
        target: 'ws://127.0.0.1:7781',
        ws: true,
      },
      // HTTP API 同源代理: 前端默认 backend=location.origin, dev 下
      // 全部端点转发到 7781 (与生产同源行为一致).
      // /run 是前缀匹配, 已覆盖 /run_routine.
      '/run': 'http://127.0.0.1:7781',
      '/agents': 'http://127.0.0.1:7781',
      '/routines': 'http://127.0.0.1:7781',
      '/upload': 'http://127.0.0.1:7781',
      '/files': 'http://127.0.0.1:7781',
      '/health': 'http://127.0.0.1:7781',
      '/library_skills': 'http://127.0.0.1:7781',
    },
  },
})
