// @vitest-environment node
/**
 * 前端 API 接口 —— 真实后端集成测试（"一个都不要拉下"）
 *
 * 覆盖 `frontend/src/api/*.ts` 暴露的全部接口 + 组件里直接 fetch 的接口
 * + 后端仅有、前端未包装的接口（admin / config / weather / chat / voice 等）。
 *
 * 运行（后端已在 :8000 运行）：
 *   cd frontend
 *   $env:LIVE_API_BASE='http://127.0.0.1:8000'; npx vitest run tests/integration/api-live.spec.ts
 *
 * 不设 LIVE_API_BASE 时整组跳过（`npm test` 不会误触网络）。
 * LLM 相关接口（chat/multi-agent）默认执行；设 LIVE_API_LLM=0 可跳过以省 token。
 *
 * 说明：
 * - 使用 node 环境：原生 FormData/Blob/File 与 undici fetch 配套，multipart 上传可用
 *   （jsdom 的 FormData 无法被 undici 序列化 → 422）。仅补一个 localStorage 兜底
 *   （auth.ts 读它拿身份）。XHR 版 `uploadDocument` 另见 `api-live-upload.spec.ts`（jsdom）。
 * - 通过挂载全局 fetch 代理把相对 `/api/...` 重写为 LIVE_API_BASE，并保留 JS 调用栈
 *   里前端封装函数的真实行为（认证头仍由 `addAuthHeaders` 注入）。
 * - 写接口一律「自建自删」/「导出再导入」/「开→关还原」，尽量不留下副作用。
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import os from 'node:os'
import fs from 'node:fs'
import path from 'node:path'

// node 环境无 localStorage：补最小兜底（auth.ts 从中读取身份）
if (typeof (globalThis as any).localStorage === 'undefined') {
  const store = new Map<string, string>()
  ;(globalThis as any).localStorage = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => { store.set(k, String(v)) },
    removeItem: (k: string) => { store.delete(k) },
    clear: () => store.clear(),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    get length() { return store.size },
  }
}

const LIVE = (process.env.LIVE_API_BASE || '').replace(/\/+$/, '')
const RUN = !!LIVE
const RUN_LLM = process.env.LIVE_API_LLM !== '0'

const UID = 'u-apitester'
const SECRET = 'apitester-device-secret-0001'
let TOKEN = ''
let EXP = 0

const T_QUICK = 15000
const T_WRITE = 40000
const T_HEAVY = 200000

// ── 全局 fetch 代理：相对路径 → LIVE_API_BASE ──────────────────────────────
const realFetch = globalThis.fetch.bind(globalThis)
function installFetchProxy() {
  const g = globalThis as any
  if (g.__apiLiveProxyInstalled) return
  const prev = g.fetch
  g.fetch = ((input: any, init?: any) => {
    let url = input
    if (typeof input === 'string' && input.startsWith('/')) {
      url = LIVE + input
    } else if (input instanceof URL && input.pathname.startsWith('/')) {
      url = LIVE + input.pathname + input.search
    }
    return prev(url, init)
  }) as typeof fetch
  g.__apiLiveProxyInstalled = true
}

// ── 原始 HTTP 辅助（用于组件/后端独有接口）─────────────────────────────────
function authHeaders(json = false): Record<string, string> {
  const h: Record<string, string> = { 'X-User-Id': UID }
  if (TOKEN) h['X-Auth-Token'] = TOKEN
  if (json) h['Content-Type'] = 'application/json'
  return h
}
async function raw(method: string, apiPath: string, body?: any): Promise<Response> {
  const init: RequestInit = { method, headers: authHeaders(body !== undefined) }
  if (body !== undefined) init.body = typeof body === 'string' ? body : JSON.stringify(body)
  return realFetch(LIVE + apiPath, init)
}

// ── 动态加载前端 API 模块（在 localStorage 就绪后再加载，避免竞态）────────
let auth: typeof import('@/api/auth')
let docs: typeof import('@/api/documents')
let vectors: typeof import('@/api/vectors')
let skills: typeof import('@/api/skills')
let plugins: typeof import('@/api/plugins')
let sessions: typeof import('@/api/sessions')
let multiAgent: typeof import('@/api/multiAgent')
let models: typeof import('@/api/models')
let monitor: typeof import('@/api/monitor')
let logs: typeof import('@/api/logs')
let generated: typeof import('@/api/generated')
let customTools: typeof import('@/api/customTools')
let permission: typeof import('@/api/permission')
let voice: typeof import('@/api/voice')

beforeAll(async () => {
  if (!RUN) return
  // 1) 设备身份注册 + 换 token（幂等：同一 secret 重复注册通过）
  await raw('POST', '/api/auth/register', { user_id: UID, device_secret: SECRET })
  const tokRes = await raw('POST', '/api/auth/token', { user_id: UID, device_secret: SECRET })
  const tokJson = await tokRes.json()
  TOKEN = tokJson?.data?.token || ''
  EXP = tokJson?.data?.expires_at || Math.floor(Date.now() / 1000) + 3600

  // 2) 把会话写入 localStorage（auth.ts 的 addAuthHeaders 从这里读取）
  localStorage.setItem('agent_super_user_id', UID)
  localStorage.setItem('agent_super_username', UID)
  localStorage.setItem('agent_super_account_type', 'device')
  localStorage.setItem('agent_super_auth_token', TOKEN)
  localStorage.setItem('agent_super_auth_token_expires_at', String(EXP))

  // 3) 安装 fetch 代理 + 加载模块
  installFetchProxy()
  ;[auth, docs, vectors, skills, plugins, sessions, multiAgent, models,
    monitor, logs, generated, customTools, permission, voice] = await Promise.all([
    import('@/api/auth'), import('@/api/documents'), import('@/api/vectors'),
    import('@/api/skills'), import('@/api/plugins'), import('@/api/sessions'),
    import('@/api/multiAgent'), import('@/api/models'), import('@/api/monitor'),
    import('@/api/logs'), import('@/api/generated'), import('@/api/customTools'),
    import('@/api/permission'), import('@/api/voice'),
  ])
}, T_HEAVY)

afterAll(() => {
  // 清理无关；写接口各自自建自删
})

// ═══════════════════════════════════════════════════════════════════════════
// 0. 健康检查 / 基础
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('健康检查', () => {
  it('GET / （root）', async () => {
    const r = await realFetch(LIVE + '/')
    expect(r.ok).toBe(true)
    const j = await r.json()
    expect(j.service).toBeTruthy()
  })
  it('GET /health', async () => {
    const r = await realFetch(LIVE + '/health')
    expect(r.ok).toBe(true)
    expect((await r.json()).status).toBe('ok')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 1. Auth（auth.ts）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Auth /api/auth/*', () => {
  it('GET /api/auth/status（doInit 探测）', async () => {
    const r = await raw('GET', '/api/auth/status')
    expect(r.ok).toBe(true)
    expect(typeof (await r.json()).data.enabled).toBe('boolean')
  })
  it('POST /api/auth/register（设备注册，幂等）', async () => {
    const r = await raw('POST', '/api/auth/register', { user_id: UID, device_secret: SECRET })
    expect(r.ok).toBe(true)
  })
  it('POST /api/auth/token（设备换 token）', async () => {
    const r = await raw('POST', '/api/auth/token', { user_id: UID, device_secret: SECRET })
    expect(r.ok).toBe(true)
    expect((await r.json()).data.token).toBeTruthy()
  })
  it('POST /api/auth/token 错误 secret → 401', async () => {
    const r = await raw('POST', '/api/auth/token', { user_id: UID, device_secret: 'wrong' })
    expect(r.status).toBe(401)
  })
  it('POST /api/auth/account/register + login（账号，幂等回退）', async () => {
    const uname = 'apitester_acct'
    const pwd = 'apitester-pw-123'
    try {
      await auth.registerAccount(uname, pwd)
    } catch {
      await auth.loginAccount(uname, pwd)
    }
    expect(auth.getUserId()).toBeTruthy()
  })
  it('GET /api/auth/account/me（fetchMe）', async () => {
    const me = await auth.fetchMe()
    expect(me.user_id).toBeTruthy()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. Models（models.ts + 后端独有 export/import/reload/catalog-full）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Models /api/models/*', () => {
  let catalog: any
  it('GET /api/models（fetchModels）', async () => {
    catalog = await models.fetchModels()
    expect(Array.isArray(catalog.models)).toBe(true)
    expect(catalog.models.length).toBeGreaterThan(0)
  })
  it('GET /api/models/config（fetchModelsConfig）', async () => {
    const cfg = await models.fetchModelsConfig()
    expect(Array.isArray(cfg.providers)).toBe(true)
  })
  it('POST /api/models/estimate-tokens（text）', async () => {
    const est = await models.estimateTokens({ text: '你好，这是一个接口测试。' })
    expect(est.tokens).toBeGreaterThan(0)
  }, T_QUICK)
  it('POST /api/models/estimate-tokens（messages）', async () => {
    const est = await models.estimateTokens({ messages: [{ role: 'user', content: 'hi' }] })
    expect(est.tokens).toBeGreaterThanOrEqual(0)
  }, T_QUICK)
  it('POST + DELETE /api/models/custom（id 含 "/"，回归删除按钮 404）', async () => {
    // 回归：模型 id 普遍含 '/'（deepseek/…、ollama/llama3.1:latest）。
    // 后端 DELETE 曾用 /models/custom/{mid}（非 {path}）→ 匹配不了 '/' → 404（目录删除按钮失效）。
    // 这里用含 '/' 的自定义模型走同一条 DELETE 路由自建自删（不触碰真实内置模型）。
    const id = 'apitest/slash-model'
    // 后端返回 {model, models}（前端 saveCustomModel 的返回类型标注偏窄，此处按真实形状断言）
    const created: any = await models.saveCustomModel({ id, provider: 'apitest', name: 'Slash' })
    expect(created.model?.id ?? created.id).toBe(id)
    const removed = await models.deleteCustomModel(id)
    expect(removed.removed).toBe(true)
    // 再删应 200 且 removed:false（幂等，不 404）
    const again = await models.deleteCustomModel(id)
    expect(again.removed).toBe(false)
  }, T_HEAVY)
  it('PUT + DELETE /api/models/providers/{name}（自建自删）', async () => {
    const name = 'apitest-provider'
    // 后端返回 {provider, providers, models}（reload_catalog 探测较慢，给足超时）
    const p: any = await models.saveProvider(name, { label: 'APITest', api_base: '', api_key: '', enabled: false })
    expect(p.provider).toBeTruthy()
    expect(Array.isArray(p.providers)).toBe(true)
    const removed = await models.deleteProvider(name)
    expect(removed.removed).toBe(true)
  }, T_WRITE)
  it('PUT /api/models/defaults（回写现有默认值，不变更）', async () => {
    const cfg = await models.fetchModelsConfig()
    await models.saveDefaults(cfg.default_model, cfg.small_model, cfg.image_caption_model, cfg.voice_model_size)
    const after = await models.fetchModelsConfig()
    expect(after.default_model).toBe(cfg.default_model)
  }, T_WRITE)
  it('POST /api/models/reload（后端独有）', async () => {
    const r = await raw('POST', '/api/models/reload')
    expect(r.ok).toBe(true)
  }, T_WRITE)
  it('GET /api/models/export（后端独有）', async () => {
    const r = await raw('GET', '/api/models/export')
    expect(r.ok).toBe(true)
  })
  it('GET /api/models/catalog-full（后端独有）', async () => {
    const r = await raw('GET', '/api/models/catalog-full')
    expect(r.ok).toBe(true)
  })
  it('POST /api/models/import（导出→原样导入，幂等往返）', async () => {
    const exp = await (await raw('GET', '/api/models/export')).json()
    const r = await raw('POST', '/api/models/import', exp.data)
    expect(r.ok).toBe(true)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. Documents（documents.ts）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Documents /api/documents/*', () => {
  let uploadedId = ''
  it('GET /api/documents/（listDocuments）', async () => {
    const list = await docs.listDocuments()
    expect(Array.isArray(list.documents)).toBe(true)
  })
  // 注意：XHR 版 `docs.uploadDocument` 需 jsdom（见 api-live-upload.spec.ts）；
  // 此处用等价 multipart（字段名 file）验证后端上传/入库契约。
  it('POST /api/documents/upload（multipart，字段 file）', async () => {
    const content = '接口测试文档\n第1章 测试\n这是自动化集成测试生成的临时文档，用于验证上传/入库/删除全链路。'
    const form = new FormData()
    form.append('file', new Blob([new TextEncoder().encode(content)], { type: 'text/plain' }), 'apitest-doc.txt')
    const r = await realFetch(LIVE + '/api/documents/upload', { method: 'POST', headers: authHeaders(), body: form })
    expect(r.ok).toBe(true)
    const j = await r.json()
    const taskId = j.task_id || j.data?.task_id
    expect(taskId).toBeTruthy()
    // 轮询任务直到完成（后端处理为异步）
    let doc: any = null
    for (let i = 0; i < 200; i++) {
      const tr = await raw('GET', `/api/documents/tasks/${taskId}`)
      if (tr.ok) {
        const t = await tr.json()
        if (t.status === 'completed') { doc = t.result; break }
        if (t.status === 'failed') throw new Error(t.error || 'upload task failed')
      }
      await new Promise(res => setTimeout(res, 400))
    }
    expect(doc?.id).toBeTruthy()
    uploadedId = doc.id
  }, T_HEAVY)
  it('文档出现在列表里', async () => {
    const list = await docs.listDocuments()
    expect(list.documents.some(d => d.id === uploadedId)).toBe(true)
  })
  it('GET /api/documents/tasks/{id} 不存在任务 → 404', async () => {
    const r = await raw('GET', '/api/documents/tasks/nonexistent-task-id')
    expect(r.status).toBe(404)
  })
  it('DELETE /api/documents/{id}（deleteDocument，清理）', async () => {
    const res = await docs.deleteDocument(uploadedId)
    expect(res.message).toBeTruthy()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. Vectors（vectors.ts）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Vectors /api/vectors/*', () => {
  it('GET /api/vectors/（listChunks，分页）', async () => {
    const chunks = await vectors.listChunks(0, 10)
    expect(Array.isArray(chunks.chunks)).toBe(true)
    expect(chunks.offset).toBe(0)
  })
  it('GET /api/vectors/（带 query 全文过滤）', async () => {
    const chunks = await vectors.listChunks(0, 5, undefined, '测试')
    expect(Array.isArray(chunks.chunks)).toBe(true)
  })
  it('GET /api/vectors/config（getVectorConfig）', async () => {
    const cfg = await vectors.getVectorConfig()
    expect(typeof cfg.count).toBe('number')
    expect(cfg.index_states).toBeDefined()
  })
  it('POST /api/vectors/clear-expired（clearExpiredVectors）', async () => {
    const r = await vectors.clearExpiredVectors()
    expect(r.message).toBeTruthy()
  })
  it('POST /api/vectors/repair（repairVectors）', async () => {
    const r = await vectors.repairVectors()
    expect(r.message).toBeTruthy()
  })
  it('POST /api/vectors/clear（clearVectors；当前库为空，安全）', async () => {
    const r = await vectors.clearVectors()
    expect(r.message).toBeTruthy()
  }, T_WRITE)
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. Skills（skills.ts）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Skills /api/skills/*', () => {
  const NAME = 'apitest-skill'
  it('GET /api/skills/（listSkills）', async () => {
    const list = await skills.listSkills()
    expect(Array.isArray(list)).toBe(true)
  })
  it('GET /api/skills/directory（getSkillsSources）', async () => {
    const src = await skills.getSkillsSources()
    expect(src.managed).toBeTruthy()
  })
  it('POST /api/skills/（createSkill）', async () => {
    try { await skills.deleteSkill(NAME) } catch { /* ignore stale */ }
    const s = await skills.createSkill({ name: NAME, description: 'apitest temp', content: '# apitest\n临时技能', enabled: true })
    expect(s.name).toBe(NAME)
  })
  it('GET /api/skills/{name}（getSkill）', async () => {
    const s = await skills.getSkill(NAME)
    expect(s.name).toBe(NAME)
    expect(typeof s.content).toBe('string')
  })
  it('PUT /api/skills/{name}（updateSkill）', async () => {
    const s = await skills.updateSkill(NAME, { description: 'apitest updated', content: '# updated' })
    expect(s.name).toBe(NAME)
  })
  it('POST /api/skills/{name}/toggle（toggleSkill）', async () => {
    await skills.toggleSkill(NAME, false)
    await skills.toggleSkill(NAME, true)
  })
  it('POST /api/skills/directory + /remove（外部源，自建自删）', async () => {
    const dir = os.tmpdir()
    const added = await skills.addSkillsDirectory(dir)
    expect(Array.isArray(added.extra_dirs)).toBe(true)
    const removed = await skills.removeSkillsDirectory(dir)
    expect(Array.isArray(removed.extra_dirs)).toBe(true)
  })
  it('DELETE /api/skills/{name}（deleteSkill，清理）', async () => {
    await skills.deleteSkill(NAME)
    await expect(skills.getSkill(NAME)).rejects.toBeTruthy()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. Plugins（plugins.ts + 后端独有 status / call）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Plugins /api/plugins/*', () => {
  let list: any[] = []
  it('GET /api/plugins/（listPlugins）', async () => {
    list = await plugins.listPlugins()
    expect(Array.isArray(list)).toBe(true)
    expect(list.length).toBeGreaterThan(0)
  })
  it('GET /api/plugins/{name}/status（后端独有）', async () => {
    const target = list[0]
    const r = await raw('GET', `/api/plugins/${encodeURIComponent(target.name)}/status`)
    expect(r.ok).toBe(true)
    expect(typeof (await r.json()).enabled).toBe('boolean')
  })
  it('POST /api/plugins/{name}/toggle（开→关还原）', async () => {
    const target = list.find(p => p.enabled !== undefined) || list[0]
    const orig = target.enabled
    await plugins.togglePlugin(target.name, !orig)
    await plugins.togglePlugin(target.name, orig)
  })
  it('POST /api/plugins/{name}/call/{fn} 非白名单 → 403', async () => {
    const r = await raw('POST', '/api/plugins/filesystem/call/tool_ls', { args: {} })
    expect(r.status).toBe(403)
  })
  it('POST /api/plugins/{name}/call/{fn} 白名单（example-plugin/tool_hello，需先启用）', async () => {
    // example-plugin 默认禁用 → 先启用（对齐 load_all），调用后再还原为禁用
    const before = list.find(p => p.name === 'example-plugin')
    const wasEnabled = !!before?.enabled
    await plugins.togglePlugin('example-plugin', true)
    try {
      const r = await raw('POST', '/api/plugins/example-plugin/call/tool_hello', { args: { name: 'apitest' } })
      expect(r.ok).toBe(true)
      const j = await r.json()
      expect(JSON.stringify(j)).toContain('apitest')
    } finally {
      if (!wasEnabled) await plugins.togglePlugin('example-plugin', false)
    }
  })
  it('POST /api/plugins/weather-alert/call/tool_get_typhoon_info（WeatherAlert.vue 直连，非 api/ 包装）', async () => {
    // 前端 WeatherAlert.vue 直接 fetch 该端点（bug 回归面：直连调用最易漏测）。
    // 该工具走外网（NMC 台风数据），受限网络下插件内会抛错 → 500；两者都接受。
    const before = list.find(p => p.name === 'weather-alert')
    const wasEnabled = before ? !!before.enabled : false
    await plugins.togglePlugin('weather-alert', true)
    try {
      const r = await raw('POST', '/api/plugins/weather-alert/call/tool_get_typhoon_info', { args: {} })
      expect([200, 500]).toContain(r.status)
      if (r.status === 200) {
        expect(typeof (await r.json())).toBe('object')
      } else {
        console.warn('[skip] weather-alert 返回 500（外网受限），已容忍')
      }
    } finally {
      if (!wasEnabled) await plugins.togglePlugin('weather-alert', false)
    }
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 7. Sessions（sessions.ts，全套 CRUD + 队列 + fork/revert/compact）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Sessions /api/sessions/*', () => {
  let sid = ''
  let forkId = ''
  it('GET /api/sessions（listSessions）', async () => {
    const list = await sessions.listSessions({ kind: 'multi-agent', limit: 50 })
    expect(Array.isArray(list)).toBe(true)
  })
  it('POST /api/sessions（后端独有 create）', async () => {
    const r = await raw('POST', '/api/sessions', { title: 'apitest-session', directory: 'E:\\AgentSuper', kind: 'multi-agent' })
    expect(r.ok).toBe(true)
    sid = (await r.json()).id
    expect(sid).toBeTruthy()
  })
  it('GET /api/sessions/{id}（getSession）', async () => {
    const s = await sessions.getSession(sid)
    expect(s.id).toBe(sid)
  })
  it('PATCH /api/sessions/{id}（updateSession 改名）', async () => {
    const s = await sessions.updateSession(sid, { title: 'apitest-renamed' })
    expect(s.title).toBe('apitest-renamed')
  })
  it('GET /api/sessions/{id}/messages（getSessionMessages）', async () => {
    const msgs = await sessions.getSessionMessages(sid)
    expect(Array.isArray(msgs)).toBe(true)
  })
  it('GET /api/sessions/{id}/status（getSessionStatus）', async () => {
    const st = await sessions.getSessionStatus(sid)
    expect(st.session_id).toBe(sid)
  })
  it('GET /api/sessions/{id}/children（后端独有）', async () => {
    const r = await raw('GET', `/api/sessions/${encodeURIComponent(sid)}/children`)
    expect(r.ok).toBe(true)
  })
  it('GET /api/sessions/{id}/context（后端独有）', async () => {
    const r = await raw('GET', `/api/sessions/${encodeURIComponent(sid)}/context`)
    expect(r.ok).toBe(true)
    expect((await r.json()).session_id).toBe(sid)
  })
  it('GET /api/sessions/{id}/state（后端独有）', async () => {
    const r = await raw('GET', `/api/sessions/${encodeURIComponent(sid)}/state`)
    expect(r.ok).toBe(true)
  })
  it('POST /api/sessions/{id}/prompts（enqueueSessionPrompt）', async () => {
    const q = await sessions.enqueueSessionPrompt(sid, { message: 'apitest queued task' }, 'client-apitester-1')
    expect(q.id).toBeTruthy()
  })
  it('GET /api/sessions/{id}/prompts（listSessionPrompts）', async () => {
    const list = await sessions.listSessionPrompts(sid)
    expect(Array.isArray(list)).toBe(true)
  })
  it('DELETE /api/sessions/{id}/prompts/{pid}（removeSessionPrompt）', async () => {
    await sessions.removeSessionPrompt(sid, 'client-apitester-1')
  })
  it('POST /api/sessions/{id}/fork（forkSession）', async () => {
    const f = await sessions.forkSession(sid)
    forkId = f.id
    expect(forkId).toBeTruthy()
  })
  it('POST /api/sessions/{id}/interrupt（interruptSession）', async () => {
    await sessions.interruptSession(sid)
  })
  it('POST /api/sessions/{id}/compact（compactSession）', async () => {
    await sessions.compactSession(sid)
  }, T_WRITE)
  it('POST /api/sessions/{id}/revert（revertSession）', async () => {
    const r = await sessions.revertSession(sid, 'nonexistent-message-id')
    expect(typeof r.deleted).toBe('number')
  })
  it('listConversations / getConversation（便捷封装）', async () => {
    const convs = await sessions.listConversations('multi-agent')
    expect(Array.isArray(convs)).toBe(true)
    const detail = await sessions.getConversation(sid)
    expect(detail.id).toBe(sid)
  })
  it('DELETE /api/sessions/{id}（deleteSession 清理 fork）', async () => {
    await sessions.deleteSession(forkId)
  })
  it('DELETE /api/sessions/{id}（deleteSession 清理主会话）', async () => {
    await sessions.deleteSession(sid)
    await expect(sessions.getSession(sid)).rejects.toBeTruthy()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 8. Multi-agent（multiAgent.ts：restore/diff/stream + 非流式）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Multi-Agent /api/chat/multi-agent*', () => {
  it('POST /api/chat/multi-agent/restore-snapshot（无快照 → 幂等返回）', async () => {
    const r = await multiAgent.restoreSnapshot('apitest-conv', 'apitest-msg')
    expect(r.restored).toBe(false)
  })
  it('POST /api/chat/multi-agent/diff（无快照 → 空 diff）', async () => {
    const r = await multiAgent.fetchTurnDiff('apitest-conv', 'apitest-msg')
    expect(Array.isArray(r.files)).toBe(true)
  })
  it.skipIf(!RUN_LLM)('POST /api/chat/multi-agent（非流式，LLM）', async () => {
    const r = await raw('POST', '/api/chat/multi-agent', { message: '只回复两个字：收到', use_vector_db: false })
    expect(r.ok).toBe(true)
    const j = await r.json()
    expect(j.code).toBe(0)
    expect(j.data.conversation_id).toBeTruthy()
    expect(typeof j.data.answer).toBe('string')
  }, T_HEAVY)
  it.skipIf(!RUN_LLM)('POST /api/chat/multi-agent（body.model 真实透传给上游）', async () => {
    // 回归护栏：此前 body.model 只写进会话元数据、不参与调用（改密钥/换模型都不生效），
    // 上游永远拿到 .env 默认模型 → 传一个不存在的模型 id 也会「成功」。
    // 现在必须真实透传 → 上游报错信息里应出现该模型 id。
    const bogus = 'deepseek/nonexistent-xyz-zzz'
    const r = await raw('POST', '/api/chat/multi-agent', { message: 'hi', model: bogus, use_vector_db: false })
    const j = await r.json()
    expect(j.code).not.toBe(0)
    expect(String(j.message)).toContain('nonexistent-xyz-zzz')
  }, T_HEAVY)
  it.skipIf(!RUN_LLM)('POST /api/chat/multi-agent/stream（SSE，sendMultiAgentStream）', async () => {
    const events: any[] = []
    let gotDone = false
    await multiAgent.sendMultiAgentStream(
      { message: '只回复两个字：收到', use_vector_db: false },
      (ev) => { events.push(ev); if (ev.type === 'done' || ev.type === 'error') gotDone = true },
    )
    expect(gotDone).toBe(true)
    expect(events.length).toBeGreaterThan(0)
    expect(events.some((e) => e.type === 'done')).toBe(true)
  }, T_HEAVY)
})

// ═══════════════════════════════════════════════════════════════════════════
// 9. Chat（后端平台 chat，非流式 + SSE）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN || !RUN_LLM)('Chat /api/chat*', () => {
  it('POST /api/chat（非流式）', async () => {
    const r = await raw('POST', '/api/chat', { message: '你好', user_id: UID })
    expect(r.status).toBe(200)
    const j = await r.json()
    expect('output' in j).toBe(true)
    expect(typeof j.output).toBe('string')
  }, T_HEAVY)
  it('POST /api/chat/stream（SSE）', async () => {
    const r = await raw('POST', '/api/chat/stream', { message: '你好', user_id: UID })
    expect(r.status).toBe(200)
    const text = await r.text()
    expect(text.length).toBeGreaterThan(0)
    expect(text).toContain('event:')
  }, T_HEAVY)
})

// ═══════════════════════════════════════════════════════════════════════════
// 10. Monitor（monitor.ts）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Monitor /api/monitor/*', () => {
  it('GET /api/monitor/stats（fetchStats）', async () => {
    const s = await monitor.fetchStats()
    expect(s).toBeTruthy()
  })
  it('GET /api/monitor/usage（fetchUsage）', async () => {
    const u = await monitor.fetchUsage()
    expect(typeof u.sessions).toBe('number')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 11. Logs（logs.ts）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Logs /api/logs/*', () => {
  let traceId = ''
  it('GET /api/logs?view=traces（listTraces）', async () => {
    const p = await logs.listTraces({}, 0, 10)
    expect(typeof p.total).toBe('number')
    traceId = p.traces?.[0]?.trace_id || ''
  })
  it('GET /api/logs?view=entries（listEntries）', async () => {
    const p = await logs.listEntries({}, 0, 10)
    expect(typeof p.total).toBe('number')
  })
  it('GET /api/logs/traces/{id}（getTrace）', async () => {
    const r = await logs.getTrace(traceId || 'apitest-trace', 100)
    expect(typeof r.total).toBe('number')
  })
  it('GET /api/logs/stats（getStats）', async () => {
    const s = await logs.getStats({})
    expect(typeof s.total).toBe('number')
  })
  it('GET /api/logs/filters（getFilterOptions）', async () => {
    const f = await logs.getFilterOptions()
    expect(Array.isArray(f.components)).toBe(true)
  })
  it('POST /api/logs/cleanup（cleanupLogs）', async () => {
    const r = await logs.cleanupLogs()
    expect(r).toBeTruthy()
  })
  it('POST /api/logs/clear（clearLogs，空 payload 不删）', async () => {
    const r = await logs.clearLogs({})
    expect(typeof r.deleted).toBe('number')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 12. Generated files（generated.ts）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Generated /api/generated/*', () => {
  const FNAME = '__apitest_generated.txt'
  const IMG = '__apitest_image.png'
  // 1x1 PNG（有效图片；用于验证 .png 下载路径 + content-type）
  const PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC'
  const GEN_DIR = path.resolve(process.cwd(), '..', 'backend', 'data', 'generated')
  beforeAll(() => {
    fs.mkdirSync(GEN_DIR, { recursive: true })
    fs.writeFileSync(path.join(GEN_DIR, FNAME), 'apitest generated content', 'utf-8')
    fs.writeFileSync(path.join(GEN_DIR, IMG), Buffer.from(PNG_B64, 'base64'))
  })
  afterAll(() => {
    for (const f of [FNAME, IMG]) {
      try { fs.rmSync(path.join(GEN_DIR, f), { force: true }) } catch { /* noop */ }
    }
  })
  it('GET /api/generated/（listGenerated）', async () => {
    const list = await generated.listGenerated()
    expect(Array.isArray(list.files)).toBe(true)
  })
  it('GET /api/generated/?q=（关键字过滤）', async () => {
    const list = await generated.listGenerated('__apitest')
    expect(list.files.some(f => f.filename === FNAME)).toBe(true)
    expect(list.files.some(f => f.filename === IMG)).toBe(true)
  })
  it('GET /api/generated/download/{f}（getGeneratedContent，文本）', async () => {
    const content = await generated.getGeneratedContent(FNAME)
    expect(content).toContain('apitest generated content')
  })
  it('GET /api/generated/download/{img}.png → image/png（图片路径）', async () => {
    const r = await raw('GET', '/api/generated/download/' + IMG)
    expect(r.status).toBe(200)
    expect(r.headers.get('content-type') || '').toContain('image/png')
    const buf = Buffer.from(await r.arrayBuffer())
    expect(buf.length).toBeGreaterThan(0)
    expect(buf[0]).toBe(0x89)
    expect(buf.toString('ascii', 1, 4)).toBe('PNG')
  })
  it('GET /api/generated/download/{穿越路径} → 404（防逃逸）', async () => {
    const r = await raw('GET', '/api/generated/download/..%2F..%2F..%2F%2Eenv')
    expect(r.status).toBe(404)
  })
  it('DELETE /api/generated/{f}（deleteGenerated，清理）', async () => {
    await generated.deleteGenerated(FNAME)
    await generated.deleteGenerated(IMG)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 13. Custom tools（customTools.ts）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Custom tools /api/custom-tools/*', () => {
  const NAME = 'apitest_echo'
  const SCRIPT = [
    'def tool_apitest_echo(text: str = "") -> str:',
    '    """Echo back the given text (apitest)."""',
    '    return "echo:" + str(text)',
    '',
  ].join('\n')
  let pinnedName = ''
  it('GET /api/custom-tools/（listCustomTools）', async () => {
    const list = await customTools.listCustomTools()
    expect(Array.isArray(list)).toBe(true)
  })
  it('GET /api/custom-tools/catalog（getToolCatalog）', async () => {
    const cat = await customTools.getToolCatalog()
    expect(Array.isArray(cat)).toBe(true)
    pinnedName = cat[0]?.name || ''
  })
  it('POST /api/custom-tools/script（createScriptTool）', async () => {
    try { await customTools.deleteCustomTool(NAME) } catch { /* ignore */ }
    const item = await customTools.createScriptTool({ name: NAME, description: 'apitest temp', script: SCRIPT, enabled: true })
    expect(item.name).toBe(NAME)
  })
  it('POST /api/custom-tools/{name}/toggle（toggleCustomTool）', async () => {
    await customTools.toggleCustomTool(NAME, false)
    await customTools.toggleCustomTool(NAME, true)
  })
  it('POST /api/custom-tools/pin（pinTool，自建自删）', async () => {
    if (!pinnedName) return
    const pin = await customTools.pinTool({ tool_name: pinnedName, description: 'apitest pin' })
    expect(pin.name).toBe(pinnedName)
    await customTools.deleteCustomTool(pinnedName)
  })
  it('DELETE /api/custom-tools/{name}（deleteCustomTool，清理）', async () => {
    await customTools.deleteCustomTool(NAME)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 14. Permission（permission.ts）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Permission /api/permission/*', () => {
  const WS = path.resolve(os.tmpdir(), 'apitest-workspace')
  it('GET /api/permission/pending（fetchPendingRequests）', async () => {
    const r = await permission.fetchPendingRequests()
    expect(Array.isArray(r.pending)).toBe(true)
  })
  it('POST /api/permission/request/{id}/respond 不存在 → 404', async () => {
    await expect(permission.respondToRequest('nonexistent-req', 'allowed')).rejects.toBeTruthy()
  })
  it('GET /api/permission/workspaces（fetchWorkspaces）', async () => {
    const r = await permission.fetchWorkspaces()
    expect(Array.isArray(r.workspaces)).toBe(true)
  })
  it('GET /api/permission/browse（根/盘符）', async () => {
    const r = await permission.browseDirectories('')
    expect(Array.isArray(r.dirs)).toBe(true)
  })
  it('GET /api/permission/browse（具体目录）', async () => {
    const r = await permission.browseDirectories(process.cwd())
    expect(r.path).toBeTruthy()
  })
  it('POST + DELETE /api/permission/workspaces（addWorkspace/removeWorkspace，自建自删）', async () => {
    fs.mkdirSync(WS, { recursive: true })
    const add = await permission.addWorkspace(WS)
    expect(add.status).toBe('ok')
    const rem = await permission.removeWorkspace(WS)
    expect(rem.status).toBe('ok')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 15. Voice（voice.ts）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Voice /api/voice/*', () => {
  it('GET /api/voice/status（fetchWithTimeout 直连）', async () => {
    const r = await realFetch(LIVE + '/api/voice/status', { headers: authHeaders() })
    expect(r.ok).toBe(true)
    const j = await r.json()
    expect('enabled' in j.data).toBe(true)
  })
  it('POST /api/voice/message（uploadVoiceMessage）', async () => {
    const blob = new Blob([new Uint8Array([26, 69, 223, 163, 1, 0, 0, 0])], { type: 'audio/webm' })
    const up = await voice.uploadVoiceMessage(blob, 1.5, [0.1, 0.2, 0.3], 'apitest')
    expect(up.url).toContain('/api/voice/audio/')
  }, T_WRITE)
  it('GET /api/voice/audio/{name}（voiceAudioUrl 回放）', async () => {
    const blob = new Blob([new Uint8Array([1, 2, 3, 4])], { type: 'audio/webm' })
    const up = await voice.uploadVoiceMessage(blob, 1.2, [], '')
    const url = voice.voiceAudioUrl(up.url)
    const r = await realFetch(LIVE + url, { headers: authHeaders() })
    expect(r.ok).toBe(true)
  }, T_WRITE)
  it('POST /api/voice/transcribe（未启用 → 抛错）', async () => {
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/webm' })
    await expect(voice.transcribeAudio(blob, 'apitest.webm')).rejects.toBeTruthy()
  }, T_WRITE)
  it('POST /api/voice/tts（未启用 → 抛错）', async () => {
    await expect(voice.synthesize('测试', 'Vivian', 'Chinese')).rejects.toBeTruthy()
  }, T_WRITE)
  it('voiceAudioUrl：完整 url / 裸 id 均可回放（剥路径 + 编码）', () => {
    expect(voice.voiceAudioUrl('/api/voice/audio/abc.webm')).toContain('/audio/abc.webm')
    expect(voice.voiceAudioUrl('abc.webm')).toContain('/audio/abc.webm')
    expect(voice.voiceAudioUrl('a b.webm')).toContain('a%20b.webm')
  })
  it('GET /api/voice/audio/{不存在} → 404 信封', async () => {
    const r = await realFetch(LIVE + '/api/voice/audio/__apitest_missing__.webm', { headers: authHeaders() })
    expect(r.status).toBe(404)
    const j = await r.json()
    expect(j.code).not.toBe(0)
  })
  it('POST /api/voice/message（时长无效 → 400 抛错）', async () => {
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/webm' })
    await expect(voice.uploadVoiceMessage(blob, 0, [], '')).rejects.toBeTruthy()
  }, T_WRITE)
})

// ═══════════════════════════════════════════════════════════════════════════
// 16. Config / Weather / Admin（后端独有）
// ═══════════════════════════════════════════════════════════════════════════
describe.skipIf(!RUN)('Config /api/config/*', () => {
  it('GET /api/config/summarization', async () => {
    const r = await raw('GET', '/api/config/summarization')
    expect(r.ok).toBe(true)
    expect('enabled' in (await r.json())).toBe(true)
  })
  it('POST /api/config/summarization（回写现有 keep_messages）', async () => {
    const cur = await (await raw('GET', '/api/config/summarization')).json()
    const r = await raw('POST', '/api/config/summarization', { keep_messages: cur.keep_messages })
    expect(r.ok).toBe(true)
  })
})

describe.skipIf(!RUN)('Weather /api/weather*', () => {
  it('GET /api/weather（缓存，可能 503）', async () => {
    const r = await raw('GET', '/api/weather')
    expect([200, 503]).toContain(r.status)
  })
  it('POST /api/weather/refresh', async () => {
    const r = await raw('POST', '/api/weather/refresh', { city: '北京' })
    expect([200, 503]).toContain(r.status)
  }, T_HEAVY)
})

describe.skipIf(!RUN)('Admin /api/admin/*', () => {
  it('GET /api/admin/health', async () => {
    const r = await raw('GET', '/api/admin/health')
    expect(r.ok).toBe(true)
  })
  it('GET /api/admin/tools', async () => {
    const r = await raw('GET', '/api/admin/tools')
    expect(r.ok).toBe(true)
  })
  it('GET /api/admin/agents', async () => {
    const r = await raw('GET', '/api/admin/agents')
    expect(r.ok).toBe(true)
  })
  it('GET /api/admin/prompts', async () => {
    const r = await raw('GET', '/api/admin/prompts')
    expect(r.ok).toBe(true)
  })
  it('GET /api/admin/metrics', async () => {
    const r = await raw('GET', '/api/admin/metrics')
    expect(r.ok).toBe(true)
  })
  it('GET /api/admin/approvals', async () => {
    const r = await raw('GET', '/api/admin/approvals')
    expect(r.ok).toBe(true)
  })
  it('POST /api/admin/approvals/{id} 不存在 → 404', async () => {
    const r = await raw('POST', '/api/admin/approvals/nonexistent-approval', { approved: true })
    expect(r.status).toBe(404)
  })
})
