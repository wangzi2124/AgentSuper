import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../src/api/fetch', () => ({
  fetchWithTimeout: vi.fn(),
}))

import { fetchWithTimeout } from '../../../src/api/fetch'
import { extractAttachmentText } from '../../../src/api/attachments'

const mockedFetch = vi.mocked(fetchWithTimeout)

const payload = {
  filename: 'spec.pdf',
  mime_type: 'application/pdf',
  data: 'QUJD',
}

describe('attachments api', () => {
  beforeEach(() => mockedFetch.mockReset())

  it('returns extracted text and truncated flag', async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ filename: 'spec.pdf', text: '9.10 接口', truncated: false, chars: 6 }),
    } as Response)

    const out = await extractAttachmentText(payload)
    expect(out.text).toBe('9.10 接口')
    expect(out.truncated).toBe(false)
    expect(mockedFetch).toHaveBeenCalledWith(
      '/api/attachments/extract',
      expect.objectContaining({ method: 'POST' }),
      60_000,
    )
  })

  it('surfaces backend detail on failure', async () => {
    mockedFetch.mockResolvedValue({
      ok: false,
      statusText: 'Unprocessable Entity',
      json: async () => ({ detail: '无法解析该附件内容' }),
    } as Response)

    await expect(extractAttachmentText(payload)).rejects.toThrow('无法解析该附件内容')
  })
})
