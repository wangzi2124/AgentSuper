import { createApp } from 'vue'
import { createPinia } from 'pinia'
import Vant from 'vant'
import 'vant/lib/index.css'
import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores/auth'
import './style.css'
import './styles/mobile.css'

// 创建 Vue 应用实例
const app = createApp(App)
// 注册 Pinia 状态管�?
app.use(createPinia())
// 注册 Vant（移动端组件库）
app.use(Vant)
// 注册路由
app.use(router)

// 启动身份初始化（后端启用时校验/恢复登录态，不阻塞首屏渲染）
void useAuthStore().init()

// 挂载到 DOM
app.mount('#app')
