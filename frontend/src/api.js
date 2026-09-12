const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

async function call(path, options = {}) {
  const res = await fetch(`${BASE}/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status} ${body.slice(0, 300)}`)
  }
  return res.json()
}

export const api = {
  health:       ()            => call('/health'),
  stats:        ()            => call('/stats'),
  ask:          (body)        => call('/hey-kivi/ask', { method: 'POST', body: JSON.stringify(body) }),
  dictate:      (body)        => call('/dictation',    { method: 'POST', body: JSON.stringify(body) }),
  interactions: (limit = 25)  => call(`/interactions?limit=${limit}`),
  knows:        (topic = '')  => call(`/what-kivi-knows?topic=${encodeURIComponent(topic)}`),
  memories:     (q = '')      => call(`/memories${q}`),
  memory:       (id)          => call(`/memories/${id}`),
  forget:       (description) => call('/memories/forget',  { method: 'POST', body: JSON.stringify({ description }) }),
  correct:      (description, new_claim) =>
                                 call('/memories/correct', { method: 'POST', body: JSON.stringify({ description, new_claim }) }),
  trace:        (turnId)      => call(`/turns/${turnId}/trace`),
}
