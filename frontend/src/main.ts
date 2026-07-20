import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './style.css'

// 创建 Vue 应用实例
const app = createApp(App)
// 注册 Pinia 状态管理
app.use(createPinia())
// 注册路由
app.use(router)
// 挂载到 DOM
app.mount('#app')
