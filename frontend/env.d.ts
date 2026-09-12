/// <reference types="vite/client" />

interface ImportMetaEnv {
  // [部署] 前后端分开部署：API 基址（留空=同源反代）
  readonly VITE_API_BASE?: string
  // [部署] 后端 ADMIN_TOKEN 对应值（配了才需要）
  readonly VITE_ADMIN_TOKEN?: string
  // 语音 TTS 基址 / 音色
  readonly VITE_TTS_BASE?: string
  readonly VITE_TTS_SPEAKER?: string
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}
