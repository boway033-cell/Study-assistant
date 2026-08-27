import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { cpSync, createReadStream, existsSync, statSync } from 'node:fs'
import { extname, resolve, sep } from 'node:path'

const pdfAssetRoots = {
  cmaps: resolve('node_modules/pdfjs-dist/cmaps'),
  standard_fonts: resolve('node_modules/pdfjs-dist/standard_fonts'),
  wasm: resolve('node_modules/pdfjs-dist/wasm'),
}

const pdfAssetType = file => ({
  '.bcmap': 'application/octet-stream', '.pfb': 'application/octet-stream',
  '.wasm': 'application/wasm', '.js': 'text/javascript; charset=utf-8',
}[extname(file).toLowerCase()] || 'application/octet-stream')

// 生产构建复制按需资源；开发服务器直接从 node_modules 只读提供，避免仓库重复保存约 3.3 MB 文件。
function copyPdfAssets() {
  return {
    name: 'copy-pdf-assets',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const match = String(req.url || '').match(/^\/(cmaps|standard_fonts|wasm)\/([^?#]+)$/)
        if (!match) return next()
        const root = pdfAssetRoots[match[1]]
        const file = resolve(root, decodeURIComponent(match[2]))
        if (!file.startsWith(root + sep) || !existsSync(file) || !statSync(file).isFile()) return next()
        res.statusCode = 200
        res.setHeader('Content-Type', pdfAssetType(file))
        createReadStream(file).pipe(res)
      })
    },
    closeBundle() {
      for (const [name, source] of Object.entries(pdfAssetRoots)) {
        cpSync(source, resolve('dist', name), { recursive: true })
      }
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
        target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('pdfjs-dist')) return 'pdfjs'
          if (id.includes('echarts')) return 'echarts'
          if (id.includes('element-plus')) return 'element-plus'
          if (id.includes('marked') || id.includes('dompurify')) return 'content-tools'
        },
      },
    },
  }
})
