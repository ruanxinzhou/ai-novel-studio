import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端 FastAPI 默认 8321；前端开发服务器代理 /api 与直接路径
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/health': 'http://127.0.0.1:8321',
      '/providers': 'http://127.0.0.1:8321',
      '/generate': 'http://127.0.0.1:8321',
      '/api': 'http://127.0.0.1:8321',
    },
  },
})
