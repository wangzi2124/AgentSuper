export interface FeatureNavItem {
  path: string
  label: string
  icon: string
}

export const FEATURE_NAV: FeatureNavItem[] = [
  { path: '/documents', label: '文档管理', icon: 'M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-7-7ZM13 2v7h7' },
  { path: '/custom-tools', label: '自定义工具', icon: 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2-2 2.5-2.5Z' },
  { path: '/skills', label: '技能', icon: 'M13 2 4.5 13.5H11l-1 8.5L19.5 10H13l0-8Z' },
  { path: '/vectors', label: '向量库', icon: 'M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3Zm8 6c0 1.7-3.6 3-8 3s-8-1.3-8-3m16 6c0 1.7-3.6 3-8 3s-8-1.3-8-3M4 6v12M20 6v12' },
  { path: '/generated', label: '生成文件', icon: 'M4 4h9l7 7v9H4V4Zm9 0v7h7M8 14h8M8 17h5' },
  { path: '/monitoring', label: '系统监控', icon: 'M3 12h4l3-9 4 18 3-9h4' },
  { path: '/logs', label: '日志管理', icon: 'M4 5h16M4 10h16M4 15h10M4 20h7' },
  { path: '/model-manager', label: '模型管理', icon: 'M12 2 3 7v10l9 5 9-5V7l-9-5Zm0 0v20M3 7l9 5 9-5' },
]

const PAGE_TITLES: Record<string, string> = {
  '/documents': '文档管理',
  '/custom-tools': '自定义工具',
  '/skills': '技能',
  '/vectors': '向量库',
  '/generated': '生成文件',
  '/monitoring': '系统监控',
  '/logs': '日志管理',
  '/model-manager': '模型管理',
  '/plugins': '插件管理',
}

export function pageTitle(path: string): string {
  return PAGE_TITLES[path] || 'AgentSuper'
}
