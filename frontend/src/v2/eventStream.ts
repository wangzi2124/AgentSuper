import type { V2Event } from '../contracts/v2'
import { addAuthHeaders } from '../api/fetch'

export interface ParsedFrame {
  id?: string
  event?: string
  data?: string
}

export function parseSseBuffer(buffer: string): { frames: ParsedFrame[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, '\n')
  const blocks = normalized.split('\n\n')
  const rest = blocks.pop() ?? ''
  const frames: ParsedFrame[] = []
  for (const block of blocks) {
    if (!block.trim()) continue
    const frame: ParsedFrame = {}
    for (const line of block.split('\n')) {
      if (line.startsWith(':')) continue
      const index = line.indexOf(':')
      const key = index >= 0 ? line.slice(0, index) : line
      let value = index >= 0 ? line.slice(index + 1) : ''
      if (value.startsWith(' ')) value = value.slice(1)
      if (key === 'id') frame.id = value
      else if (key === 'event') frame.event = value
      else if (key === 'data') frame.data = frame.data ? `${frame.data}\n${value}` : value
    }
    if (frame.event || frame.data) frames.push(frame)
  }
  return { frames, rest }
}

type EventHandler = (event: V2Event) => void
type ConnectedHandler = (event: V2Event) => void

const ENDPOINT = '/api/v2/event'

class V2EventStream {
  private handlers = new Set<EventHandler>()
  private connectedHandlers = new Set<ConnectedHandler>()
  private controller: AbortController | null = null
  private running = false
  private retryMs = 1000
  private sleepTimer: ReturnType<typeof setTimeout> | null = null

  subscribe(handler: EventHandler): () => void {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  onConnected(handler: ConnectedHandler): () => void {
    this.connectedHandlers.add(handler)
    return () => this.connectedHandlers.delete(handler)
  }

  start(): void {
    if (this.running) return
    this.running = true
    void this.loop()
  }

  stop(): void {
    this.running = false
    this.controller?.abort()
    this.controller = null
    if (this.sleepTimer) {
      clearTimeout(this.sleepTimer)
      this.sleepTimer = null
    }
  }

  get connected(): boolean {
    return this.running && this.controller !== null
  }

  private dispatch(event: V2Event): void {
    if (event.type === 'server.connected') {
      for (const handler of this.connectedHandlers) handler(event)
      return
    }
    for (const handler of this.handlers) handler(event)
  }

  private async loop(): Promise<void> {
    while (this.running) {
      const controller = new AbortController()
      this.controller = controller
      try {
        const headers = await addAuthHeaders({ Accept: 'text/event-stream' })
        const response = await fetch(ENDPOINT, {
          method: 'GET',
          headers,
          signal: controller.signal,
          cache: 'no-store',
        })
        if (!response.ok || !response.body) {
          throw new Error(`event stream failed: ${response.status}`)
        }
        this.retryMs = 1000
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        while (this.running) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const { frames, rest } = parseSseBuffer(buffer)
          buffer = rest
          for (const frame of frames) {
            if (!frame.data) continue
            try {
              const event = JSON.parse(frame.data) as V2Event
              this.dispatch(event)
            } catch {
              /* ignore malformed frame */
            }
          }
        }
      } catch {
        /* reconnect below */
      } finally {
        this.controller = null
        controller.abort()
      }
      if (!this.running) break
      await this.sleep(this.retryMs)
      this.retryMs = Math.min(this.retryMs * 2, 30_000)
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => {
      this.sleepTimer = setTimeout(() => {
        this.sleepTimer = null
        resolve()
      }, ms)
    })
  }
}

let singleton: V2EventStream | null = null

export function getV2EventStream(): V2EventStream {
  if (!singleton) singleton = new V2EventStream()
  return singleton
}

export function resetV2EventStreamForTests(): void {
  singleton?.stop()
  singleton = null
}
