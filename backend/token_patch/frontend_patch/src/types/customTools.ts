// 自定义工具类型 [PATCH6]
// 前端「Skills → 自定义工具」页面使用的类型定义
// 安装:复制到 frontend/src/types/customTools.ts

// 自定义工具条目（脚本型 script / 固定型 pin）
export interface CustomToolItem {
  name: string
  type: 'script' | 'pin'
  description: string
  path: string
  enabled: boolean
  tools: string[]
}

// 工具目录条目（供「固定已有工具」下拉选择）
export interface ToolCatalogItem {
  name: string
  description: string
}
