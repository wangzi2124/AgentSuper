import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { ensureAuth } from './api/auth'
import './style.css'

// 创建 Vue 应用实例
const app = createApp(App)
// 注册 Pinia 状态管理
app.use(createPinia())
// 注册路由
app.use(router)

// 启动身份签名（后端启用时先注册/换 token，不阻塞首屏渲染）
void ensureAuth()

// 挂载到 DOM
app.mount('#app')
