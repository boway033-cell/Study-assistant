import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时代理 /api 到后端；构建产物由 FastAPI 托管
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1500
  }
})
