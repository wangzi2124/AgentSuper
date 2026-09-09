import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.spec.ts'],
    css: false,
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,vue}'],
      exclude: ['src/main.ts', 'src/**/*.d.ts'],
      reporter: ['text', 'json-summary'],
      thresholds: {
        global: { statements: 20, branches: 10, functions: 10, lines: 20 },
        // 核心可测逻辑：单测已覆盖的模块，防止回归把覆盖率打穿
        'src/stores/multiAgent.ts': { statements: 65, branches: 55, functions: 60, lines: 70 },
        'src/stores/auth.ts': { statements: 85, branches: 50, functions: 50, lines: 90 },
        'src/api/session-cache.ts': { statements: 85, branches: 85, functions: 80, lines: 90 },
        'src/components/MultiAgentResponse.vue': { statements: 80, branches: 55, functions: 55, lines: 90 },
        'src/mobile/MobileShell.vue': { statements: 50, branches: 40, functions: 40, lines: 50 },
      },
    },
  },
})
