import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mocks = vi.hoisted(() => ({ updateSession: vi.fn() }))

vi.mock('@/api/sessions', () => ({ updateSession: mocks.updateSession }))

import { useModelSwitchStore } from '@/stores/modelSwitch'
import { useSessionModelStore } from '@/stores/sessionModel'

function setup() {
  setActivePinia(createPinia())
  localStorage.clear()
  mocks.updateSession.mockReset()
  mocks.updateSession.mockResolvedValue({})
  const sessionModel = useSessionModelStore()
  const modelSwitch = useModelSwitchStore()
  sessionModel.setActive('s1', 'old/model')
  sessionModel.select('s1', 'new/model')
  return { sessionModel, modelSwitch }
}

describe('modelSwitch store（后台切换 job）', () => {
  it('成功时 PATCH 一次并更新 active', async () => {
    const { sessionModel, modelSwitch } = setup()
    const result = await modelSwitch.enqueue('s1', 'new/model')
    expect(result.status).toBe('done')
    expect(mocks.updateSession).toHaveBeenCalledTimes(1)
    expect(mocks.updateSession).toHaveBeenCalledWith('s1', {
      model: { id: 'new/model', providerID: 'new' },
    }, 120_000)
    expect(sessionModel.active('s1')).toBe('new/model')
  })

  it('失败时回滚 desired 到 active', async () => {
    const { sessionModel, modelSwitch } = setup()
    const error = Object.assign(new Error('上下文窗口未知'), { statusCode: 400 })
    mocks.updateSession.mockRejectedValue(error)

    const result = await modelSwitch.enqueue('s1', 'new/model')
    expect(result.status).toBe('failed')
    expect(sessionModel.desired('s1')).toBeUndefined()
    expect(sessionModel.active('s1')).toBe('old/model')
  })

  it('409 表示会话运行中：保留 desired，交给回合开始时切换', async () => {
    const { sessionModel, modelSwitch } = setup()
    mocks.updateSession.mockRejectedValue(Object.assign(new Error('running'), { statusCode: 409 }))

    const result = await modelSwitch.enqueue('s1', 'new/model')
    expect(result.status).toBe('deferred')
    expect(sessionModel.desired('s1')).toBe('new/model')
    expect(sessionModel.active('s1')).toBe('old/model')
  })

  it('连续选择按顺序串行，只有最新目标生效', async () => {
    const { sessionModel, modelSwitch } = setup()
    const resolvers: Array<(value: unknown) => void> = []
    mocks.updateSession.mockImplementation(
      () => new Promise(resolve => { resolvers.push(resolve) }),
    )

    const first = modelSwitch.enqueue('s1', 'mid/model')
    const second = modelSwitch.enqueue('s1', 'new/model')
    expect(mocks.updateSession).toHaveBeenCalledTimes(1)
    expect(mocks.updateSession.mock.calls[0][0]).toBe('s1')
    expect(mocks.updateSession.mock.calls[0][1].model.id).toBe('mid/model')

    resolvers[0]({})
    await first
    expect(mocks.updateSession).toHaveBeenCalledTimes(2)
    expect(mocks.updateSession.mock.calls[1][1].model.id).toBe('new/model')

    resolvers[1]({})
    await second
    expect(sessionModel.active('s1')).toBe('new/model')
    expect(modelSwitch.status('s1')).toBe('done')
  })

  it('wait 会等到队列清空', async () => {
    const { modelSwitch } = setup()
    void modelSwitch.enqueue('s1', 'new/model')
    const result = await modelSwitch.wait('s1')
    expect(result?.status).toBe('done')
    expect(mocks.updateSession).toHaveBeenCalledTimes(1)
  })

  it('forget 后不把已完成结果写回已删除会话', async () => {
    const { sessionModel, modelSwitch } = setup()
    let resolve: (value: unknown) => void = () => {}
    mocks.updateSession.mockImplementation(() => new Promise(r => { resolve = r }))

    const job = modelSwitch.enqueue('s1', 'new/model')
    modelSwitch.forget('s1')
    resolve({})
    const result = await job
    expect(result.status).toBe('superseded')
    expect(sessionModel.active('s1')).toBe('old/model')
  })

  it('draft/空 key 不触发 PATCH', async () => {
    const { modelSwitch } = setup()
    expect((await modelSwitch.enqueue('__draft__', 'new/model')).status).toBe('idle')
    expect((await modelSwitch.wait('__draft__'))).toBeNull()
    expect(mocks.updateSession).not.toHaveBeenCalled()
  })
})
