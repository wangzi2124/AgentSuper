import { describe, expect, it } from 'vitest'

import router from '../../../src/router'

describe('v2 chat routes', () => {
  it('registers new chat and conversation routes', () => {
    expect(router.hasRoute('V2Chat')).toBe(true)
    expect(router.hasRoute('V2ChatConversation')).toBe(true)
    expect(router.resolve({ name: 'V2ChatConversation', params: { id: 'ses_1' } }).path)
      .toBe('/v2/chat/ses_1')
  })
})
