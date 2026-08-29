import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
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
