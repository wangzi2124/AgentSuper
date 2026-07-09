import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: '/chat',
    },
    {
      path: '/chat',
      name: 'Chat',
      component: () => import('../views/ChatView.vue'),
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

export default router
