import { beforeEach, describe, expect, it } from 'vitest'

import {
  MODEL_CACHE_VERSION,
  loadModelCache,
  saveModelCache,
} from '../../../src/api/model-cache'

const config = {
  savedAt: 1,
  models: [],
  default_model: '',
  small_model: '',
  image_caption_model: '',
  voice_model_size: '',
}

describe('model-cache', () => {
  beforeEach(() => localStorage.clear())

  it('丢弃没有 origin 的 v1 缓存', () => {
    localStorage.setItem('agentsuper:model-config-cache:v1', JSON.stringify(config))

    expect(loadModelCache()).toBeNull()
    expect(localStorage.getItem('agentsuper:model-config-cache:v1')).toBeNull()
  })

  it('v2 写入带版本，缺版本的缓存视为无效', () => {
    saveModelCache(config)
    expect(loadModelCache()?.version).toBe(MODEL_CACHE_VERSION)

    localStorage.setItem('agentsuper:model-config-cache:v2', JSON.stringify(config))
    expect(loadModelCache()).toBeNull()
  })
})
