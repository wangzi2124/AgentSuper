import { createApp } from 'vue'
import { createPinia } from 'pinia'
import Vant from 'vant'
import 'vant/lib/index.css'
import App from './App.vue'
import router from './router'
import './styles/global.css'
import './styles/mobile.css'

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
