// @vitest-environment jsdom
// @vitest-environment-options { "url": "http://127.0.0.1:8000/" }
/**
 * `documents.uploadDocument` 专用集成测试（XHR 版）。
 *
 * 该函数用 XMLHttpRequest + 相对 URL（jsdom 文档地址 = 后端）上传 multipart，
 * 并轮询 `/api/documents/tasks/{id}` 直到完成。jsdom 的 FormData/Blob 与 XHR 配套，
 * 而 undici fetch 无法序列化 jsdom FormData —— 故 multipart 上传只能在本 jsdom 文件测。
 *
 * 运行：$env:LIVE_API_BASE='http://127.0.0.1:8000'; npx vitest run tests/integration/api-live-upload.spec.ts
 */
import { describe, it, expect, beforeAll } from 'vitest'

const LIVE = (process.env.LIVE_API_BASE || '').replace(/\/+$/, '')
const RUN = !!LIVE
const UID = 'u-apitester'
const SECRET = 'apitester-device-secret-0001'
let TOKEN = ''

const realFetch = globalThis.fetch.bind(globalThis)

let docs: typeof import('@/api/documents')

beforeAll(async () => {
  if (!RUN) return
  await realFetch(LIVE + '/api/auth/register', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: UID, device_secret: SECRET }),
  })
  const tok = await realFetch(LIVE + '/api/auth/token', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: UID, device_secret: SECRET }),
  })
  TOKEN = (await tok.json()).data.token

  localStorage.setItem('agent_super_user_id', UID)
  localStorage.setItem('agent_super_username', UID)
  localStorage.setItem('agent_super_account_type', 'device')
  localStorage.setItem('agent_super_auth_token', TOKEN)
  localStorage.setItem('agent_super_auth_token_expires_at', String(Math.floor(Date.now() / 1000) + 3600))

  // 相对 URL fetch（pollTask）→ 后端
  const g = globalThis as any
  if (!g.__uploadProxyInstalled) {
    const prev = g.fetch
    g.fetch = ((input: any, init?: any) => {
      const url = typeof input === 'string' && input.startsWith('/') ? LIVE + input : input
      return prev(url, init)
    }) as typeof fetch
    g.__uploadProxyInstalled = true
  }

  docs = await import('@/api/documents')
}, 60000)

describe.skipIf(!RUN)('documents.uploadDocument（XHR + 任务轮询）', () => {
  let docId = ''
  it('上传 → 返回文档对象', async () => {
    const content = '接口测试文档\n第1章 测试\n验证 uploadDocument(XHR) 全链路。'
    const file = new File([new TextEncoder().encode(content)], 'apitest-doc-xhr.txt', { type: 'text/plain' })
    const doc = await docs.uploadDocument(file)
    expect(doc.id).toBeTruthy()
    docId = doc.id
  }, 200000)

  it('上传后出现在列表', async () => {
    const list = await docs.listDocuments()
    expect(list.documents.some(d => d.id === docId)).toBe(true)
  })

  it('删除文档（清理）', async () => {
    const res = await docs.deleteDocument(docId)
    expect(res.message).toBeTruthy()
  })
})
