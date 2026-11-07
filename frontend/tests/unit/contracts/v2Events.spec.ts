import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import {
  DURABLE_EVENT_TYPES,
  V2_EVENT_TYPES,
  VOLATILE_EVENT_TYPES,
} from '../../../src/contracts/v2'
import type {
  InboxInfo,
  MessageV2,
  PermissionRequest,
  SessionInfoV2,
  V2Event,
} from '../../../src/contracts/v2'

const FIXTURE_DIR = resolve(process.cwd(), '../contracts/v2/fixtures')

function fixture<T>(name: string): T {
  return JSON.parse(readFileSync(resolve(FIXTURE_DIR, name), 'utf8')) as T
}

describe('v2 event contract', () => {
  it('gives every event type exactly one durability class', () => {
    expect(new Set(V2_EVENT_TYPES).size).toBe(V2_EVENT_TYPES.length)
    for (const type of V2_EVENT_TYPES) {
      const durable = DURABLE_EVENT_TYPES.has(type)
      const volatile = VOLATILE_EVENT_TYPES.has(type)
      expect(durable !== volatile, type).toBe(true)
    }
    expect(DURABLE_EVENT_TYPES.size + VOLATILE_EVENT_TYPES.size).toBe(V2_EVENT_TYPES.length)
  })

  it('keeps fixture events within the declared contract', () => {
    const events = fixture<V2Event[]>('events.json')
    expect(events.length).toBeGreaterThan(10)
    for (const event of events) {
      expect(V2_EVENT_TYPES).toContain(event.type)
      if (DURABLE_EVENT_TYPES.has(event.type)) {
        expect(event.durable, event.type).toBeTruthy()
        expect(event.durable?.seq).toBeGreaterThan(0)
      } else {
        expect(event.durable, event.type).toBeNull()
      }
    }
  })

  it('covers the required reducer input shapes', () => {
    const session = fixture<SessionInfoV2>('session.json')
    expect(session.status).toBe('idle')
    expect(session.time.created).toBeGreaterThan(0)

    const messages = fixture<MessageV2[]>('messages.json')
    const assistant = messages.find(message => message.type === 'assistant')
    expect(assistant?.type).toBe('assistant')
    if (assistant?.type === 'assistant') {
      expect(assistant.content.some(part => part.type === 'tool')).toBe(true)
      expect(assistant.content.some(part => part.type === 'text')).toBe(true)
    }

    const inbox = fixture<InboxInfo[]>('inbox.json')
    expect(inbox[0]?.item.delivery).toBe('queue')

    const permissions = fixture<PermissionRequest[]>('permissions.json')
    expect(permissions[0]?.source?.type).toBe('tool')
  })
})
