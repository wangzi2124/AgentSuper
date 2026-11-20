// 自定义工具类型 [PATCH6]
// 前端「自定义工具」页面使用的类型定义

// 自定义工具条目（脚本型）
export interface CustomToolItem {
  name: string
  type: 'script'
  description: string
  path: string
  enabled: boolean
  tools: string[]
}
