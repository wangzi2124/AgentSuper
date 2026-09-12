import { createApp } from 'vue'
import { createPinia } from 'pinia'
import Vant from 'vant'
import 'vant/lib/index.css'
import App from './App.vue'
import router from './router'
import { installApiFetch } from './api/base'
import './styles/global.css'
import './styles/mobile.css'

// [部署] 安装 API base / 管理员令牌支持（VITE_API_BASE / VITE_ADMIN_TOKEN，
// 未配置时不改动全局 fetch）。必须在任何请求发出前执行。
installApiFetch()

// 启动：等待首屏路由守卫完成（登录态校验/重定向到 /login）后再挂载，
// 避免未登录时 Sidebar/MultiAgentChatHistory 提前挂载、发出 401 鉴权请求
async function bootstrap() {
  const app = createApp(App)
  // 注册 Pinia 状态管理
  app.use(createPinia())
  // 注册 Vant（移动端组件库）
  app.use(Vant)
  // 注册路由
  app.use(router)
  await router.isReady()
  // 挂载到 DOM
  app.mount('#app')
}

void bootstrap()
