import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

// 应用路由配置
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: '/multi-agent',
    },
    {
      path: '/login',
      name: 'Login',
      component: () => import('../views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/multi-agent',
      name: 'MultiAgent',
      component: () => import('../views/MultiAgentView.vue'),
    },
    {
      path: '/multi-agent/:id',
      name: 'MultiAgentConversation',
      component: () => import('../views/MultiAgentView.vue'),
    },
    {
      path: '/documents',
      name: 'Documents',
      component: () => import('../views/DocumentsView.vue'),
    },
    {
      path: '/skills',
      name: 'Skills',
      component: () => import('../views/SkillsView.vue'),
    },
    {
      path: '/plugins',
      name: 'Plugins',
      component: () => import('../views/PluginsView.vue'),
    },
    {
      path: '/custom-tools',
      name: 'CustomTools',
      component: () => import('../views/CustomToolsView.vue'),
    },
    {
      path: '/vectors',
      name: 'Vectors',
      component: () => import('../views/VectorsView.vue'),
    },
    {
      path: '/generated',
      name: 'Generated',
      component: () => import('../views/GeneratedFilesView.vue'),
    },
    {
      path: '/monitoring',
      name: 'Monitoring',
      component: () => import('../views/MonitoringView.vue'),
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'NotFound',
      component: () => import('../views/NotFoundView.vue'),
    },
  ],
})

// 全局登录守卫：后端启用身份签名时，未登录跳转 /login；已登录访问 /login 跳回首页。
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.ready) await auth.init()
  if (!auth.enabled) return true
  if (to.meta.public) {
    return auth.isLoggedIn ? { name: 'MultiAgent' } : true
  }
  return auth.isLoggedIn ? true : { name: 'Login', query: { redirect: to.fullPath } }
})

export default router
