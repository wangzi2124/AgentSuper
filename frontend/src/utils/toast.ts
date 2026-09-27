// 轻量 toast：替代 vant 的 showToast，供桌面端组件（ChatInput 等）使用。
// 背景：vant 整包 558KB JS + 196KB CSS，此前仅为了一个 showToast 就被静态打进桌面首屏。

let host: HTMLElement | null = null
let seq = 0
const timers = new Map<number, ReturnType<typeof setTimeout>>()

function ensureHost(): HTMLElement {
  if (host && host.isConnected) return host
  host = document.createElement('div')
  host.className = 'app-toast-host'
  document.body.appendChild(host)
  return host
}

export type ToastPosition = 'top' | 'center' | 'bottom'

export function showToast(message: string, position: ToastPosition = 'center', duration = 2200): void {
  if (!message) return
  const root = ensureHost()
  const id = ++seq
  const el = document.createElement('div')
  el.className = `app-toast app-toast-${position}`
  el.dataset.toastId = String(id)
  el.textContent = message
  root.appendChild(el)
  // 下一帧加 .in 触发过渡
  requestAnimationFrame(() => el.classList.add('in'))
  const timer = setTimeout(() => {
    timers.delete(id)
    el.classList.remove('in')
    setTimeout(() => el.remove(), 220)
  }, duration)
  timers.set(id, timer)
  // 同时最多保留 3 条，超出则挤掉最早的一条
  while (root.childElementCount > 3) {
    const first = root.firstElementChild as HTMLElement | null
    if (!first) break
    const firstId = Number(first.dataset.toastId || 0)
    const t = timers.get(firstId)
    if (t) { clearTimeout(t); timers.delete(firstId) }
    first.remove()
  }
}

export function closeAllToasts(): void {
  timers.forEach((t) => clearTimeout(t))
  timers.clear()
  if (host) host.innerHTML = ''
}

const STYLE_ID = 'app-toast-style'
const CSS = `
.app-toast-host{position:fixed;left:0;right:0;z-index:99999;display:flex;flex-direction:column;align-items:center;gap:8px;pointer-events:none}
.app-toast{max-width:min(80vw,420px);padding:10px 16px;border-radius:10px;
  background:var(--surface,rgba(17,24,39,.95));color:var(--text,#f1f5f9);
  border:1px solid var(--border-subtle,rgba(148,163,184,.25));
  box-shadow:0 8px 28px rgba(0,0,0,.28);font-size:13px;line-height:1.5;text-align:center;
  opacity:0;transform:translateY(-6px);transition:opacity .2s ease,transform .2s ease}
.app-toast.in{opacity:1;transform:translateY(0)}
.app-toast-top{position:absolute;top:24px}
.app-toast-center{position:absolute;top:50%;transform:translateY(-6px) translateY(-50%)}
.app-toast-center.in{transform:translateY(-50%)}
.app-toast-bottom{position:absolute;bottom:96px}
`

export function installToastStyle(): void {
  if (typeof document === 'undefined') return
  if (document.getElementById(STYLE_ID)) return
  const el = document.createElement('style')
  el.id = STYLE_ID
  el.textContent = CSS
  document.head.appendChild(el)
}

installToastStyle()
