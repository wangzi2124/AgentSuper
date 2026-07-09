const BASE = '/api/permission'

export async function fetchPendingRequests(): Promise<{ pending: Array<{
  id: string; path: string; operation: string; tool_name: string;
  tool_args: Record<string, unknown>; created_at: string
}> }> {
  const res = await fetch(BASE + '/pending')
  if (!res.ok) throw new Error('Failed to fetch pending requests')
  return res.json()
}

export async function respondToRequest(requestId: string, decision: string, remember: boolean = false): Promise<void> {
  const res = await fetch(BASE + '/request/' + encodeURIComponent(requestId) + '/respond', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision, remember }),
  })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Respond error: ${err || res.statusText}`)
  }
}
