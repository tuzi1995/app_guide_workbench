import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时前端跑在 5178 端口，后端跑在 8095。
// /api 和 /media 都转发给后端，这样前端代码里一律写相对路径，不用关心端口。
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5178,
    strictPort: false,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8095',
        changeOrigin: true,
      },
      '/media': {
        target: 'http://127.0.0.1:8095',
        changeOrigin: true,
      },
    },
  },
})
