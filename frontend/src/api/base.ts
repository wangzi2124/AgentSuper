// [部署] API 基址与管理员令牌（可选）——支持前后端分开部署（跨域）。
//
// - VITE_API_BASE：跨域部署时把相对 `/api/...` 前缀到该地址
//   （例如 VITE_API_BASE=https://api.example.com）。留空 = 同源（走反向代理）。
// - VITE_ADMIN_TOKEN：后端 .env 配了 ADMIN_TOKEN 时，前端管理操作需带
//   `Authorization: Bearer <token>`，否则远程管理接口会 403。
//
// 构建时注入：在 frontend/.env 或 frontend/.env.production 里写上述变量。

const RAW_BASE = (import.meta.env.VITE_API_BASE as string) || ''
export const API_BASE = RAW_BASE.replace(/\/+$/, '')
export const ADMIN_TOKEN = (import.meta.env.VITE_ADMIN_TOKEN as string) || ''

/** 给相对路径加上 API_BASE 前缀（绝对 URL 原样返回）。 */
export function apiUrl(path: string): string {
  if (!API_BASE || !path.startsWith('/')) return path
  return API_BASE + path
}

/**
 * 安装全局 fetch 包装：
 *  - 把以 `/` 开头的相对 URL 前缀为 API_BASE（覆盖所有 fetch('/api/...') 调用）；
 *  - 若配置了 VITE_ADMIN_TOKEN，则为请求注入 `Authorization: Bearer <token>`。
 * 未配置任何变量时不改动全局 fetch（零副作用）。
 */
export function installApiFetch(): void {
  if (!API_BASE && !ADMIN_TOKEN) return
  const origFetch = window.fetch.bind(window)
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    let url = input
    if (typeof input === 'string' && input.startsWith('/')) {
      url = apiUrl(input)
    }
    if (ADMIN_TOKEN) {
      const headers = new Headers(init?.headers)
      if (!headers.has('Authorization')) headers.set('Authorization', `Bearer ${ADMIN_TOKEN}`)
      return origFetch(url, { ...init, headers })
    }
    return origFetch(url, init)
  }) as typeof window.fetch
}
