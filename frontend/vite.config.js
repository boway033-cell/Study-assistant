import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { cpSync } from 'node:fs'

// 把 pdf.js 的中文字体映射(cmaps)与标准字体复制到构建产物（中文 PDF 文本层必需）
function copyPdfAssets() {
  return {
    name: 'copy-pdf-assets',
    closeBundle() {
      cpSync('node_modules/pdfjs-dist/cmaps', 'dist/cmaps', { recursive: true })
      cpSync('node_modules/pdfjs-dist/standard_fonts', 'dist/standard_fonts', { recursive: true })
    },
  }
}

// 开发时代理 /api 到后端；构建产物由 FastAPI 托管
export default defineConfig({
  plugins: [vue(), copyPdfAssets()],
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
