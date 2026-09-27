// 移动端 UI 依赖（Vant）按需动态安装。
// 背景：vant 整包 558KB JS + 196KB CSS，此前 main.ts 静态 import 导致**桌面端首屏**
// 也要下载解析整个 Vant（外加 highlight.js 369KB），是首屏/路由切换卡顿的主要来源。
// 现在改为：仅当视口 ≤768px 时才动态 import 并 app.use()。
import type { App } from 'vue'

let installing: Promise<void> | null = null

/**
 * 确保 Vant 已注册到 app（幂等，多次调用共享同一个 Promise）。
 * 桌面端永不调用 → 永不加载 Vant。
 */
export function ensureVant(app: App): Promise<void> {
  if (!installing) {
    installing = Promise.all([
      import('vant'),
      import('vant/lib/index.css'),
    ]).then(([mod]) => {
      app.use(mod.default)
    })
  }
  return installing
}

/** 是否为移动端视口（与 App.vue / mobile.css 的 768px 断点保持一致）。 */
export function isMobileViewport(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false
  return window.matchMedia('(max-width: 768px)').matches
}
