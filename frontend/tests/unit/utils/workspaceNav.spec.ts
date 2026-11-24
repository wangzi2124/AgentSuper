import { beforeEach, describe, expect, it } from 'vitest'

import type { ConversationMeta } from '../../../src/api/sessions'
import {
  baseName,
  isUnder,
  loadRecentPaths,
  matchesConversation,
  normPath,
  queryTokens,
  rememberRecentPath,
  workspaceRootFor,
} from '../../../src/utils/workspaceNav'

function conv(title: string, directory?: string): ConversationMeta {
  return { id: title, title, directory, created_at: '', updated_at: '' } as ConversationMeta
}

describe('workspaceNav', () => {
  beforeEach(() => localStorage.clear())

  it('归一化路径并支持 Windows 分隔符与尾斜杠', () => {
    expect(normPath('D:\\Projects\\App\\')).toBe('d:/projects/app')
    expect(isUnder('D:\\Projects\\App\\src', 'd:/projects/app/')).toBe(true)
    expect(isUnder('D:\\Projects\\Other', 'd:/projects/app')).toBe(false)
    expect(baseName('D:/Projects/App')).toBe('App')
  })

  it('工作目录取最长匹配根', () => {
    const roots = ['D:\\projects', 'D:\\projects\\app']
    expect(workspaceRootFor('D:\\projects\\app\\src', roots)).toBe('D:\\projects\\app')
    expect(workspaceRootFor('C:\\temp', roots)).toBeNull()
  })

  it('搜索同时覆盖标题、目录路径与工作目录名', () => {
    const c = conv('设计稿', 'D:\\projects\\app\\docs')
    const tokens = queryTokens('app 设计')
    expect(matchesConversation(c, tokens, 'D:\\projects\\app')).toBe(true)
    expect(matchesConversation(c, queryTokens('docs'), 'D:\\projects\\app')).toBe(true)
    expect(matchesConversation(c, queryTokens('missing'), 'D:\\projects\\app')).toBe(false)
  })

  it('最近目录去重且限制数量', () => {
    for (let i = 0; i < 10; i++) rememberRecentPath(`D:\\repo\\${i}`)
    rememberRecentPath('d:/repo/0/')
    const paths = loadRecentPaths()
    expect(paths).toHaveLength(8)
    expect(paths[0]).toBe('d:/repo/0/')
    expect(paths.filter(p => normPath(p) === 'd:/repo/0')).toHaveLength(1)
  })
})
