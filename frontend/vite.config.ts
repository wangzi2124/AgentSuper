import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    // 仓库内存在历史误编译的同名 .js（src/**/*.js 与 .ts 并存，且已被 git 跟踪）。
    // Vite 默认解析顺序是 .js 先于 .ts → 无扩展名导入会命中过期的 .js，
    // 导致 src/**/*.ts 的修改不生效。这里显式把 .ts 提到 .js 之前。
    extensions: ['.mts', '.ts', '.tsx', '.mjs', '.js', '.jsx', '.json'],
  },
  server: {
     allowedHosts: [
      'asparagus-coziness-favorite.ngrok-free.dev',
      // 或者允许所有 ngrok 域名
      '.ngrok-free.dev'
    ],
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
