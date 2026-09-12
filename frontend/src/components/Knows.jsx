import React, { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { api } from '../api.js'

const GROUP_TITLES = {
  style_rule: 'How you write',
  decision: 'What you decided',
  commitment: 'What you promised',
  project_context: 'What you are working on',
  routine: 'How you work',
}

function Memory({ m, onChanged }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const forget = async () => {
    setBusy(true)
    try { await api.forget(m.claim); onChanged() } finally { setBusy(false) }
  }

  const scopeBits = Object.entries(m.scope || {})
    .filter(([, v]) => v)
    .map(([, v]) => v)

  return (
    <div className="memory">
      <div className="claim">{m.claim}</div>
      <div className="meta">
        <span className="meter">
          <span className="track">
            <span className="fill" style={{ width: `${Math.round(m.confidence * 54)}px` }} />
          </span>
          {m.confidence}
        </span>
        <span>
          learned from {m.evidence.length} moment{m.evidence.length === 1 ? '' : 's'}
          {m.evidence.length > 1 &&
            ` (${m.evidence[0].captured_at.slice(0, 10)} to ${m.evidence[m.evidence.length - 1].captured_at.slice(0, 10)})`}
        </span>
        <span>{m.explicitness.replace(/_/g, ' ')}</span>
        {m.status === 'provisional' && <span className="chip warn">held, not used</span>}
        {scopeBits.length > 0 && <span className="chip accent">{scopeBits.join(' / ')}</span>}
      </div>

      <div className="tools">
        <button className="ghost" onClick={() => setOpen((o) => !o)}>
          {open ? 'hide sources' : 'show sources'}
        </button>
        <button className="ghost" onClick={forget} disabled={busy}>
          {busy ? '...' : 'forget this'}
        </button>
      </div>

      {open && (
        <motion.div className="evidence" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          {m.evidence.map((e, i) => (
            <div key={i} style={{ marginBottom: '0.8rem' }}>
              <div className="quote">"{e.quote}"</div>
              <div className="src">
                {new Date(e.captured_at).toLocaleString()} - {e.app} - {e.destination}
              </div>
            </div>
          ))}
        </motion.div>
      )}
    </div>
  )
}

export default function Knows() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  const load = () => { api.knows().then(setData).catch((e) => setError(e.message)) }
  useEffect(load, [])

  if (error) return <div className="wide"><div className="err">{error}</div></div>
  if (!data) return <div className="wide"><span className="spin" /></div>

  return (
    <div className="wide">
      <p className="eyebrow"><span className="dot">-</span> what kivi knows</p>
      <h1 className="display">Field <em>notes</em><br />on how you work.</h1>
      <p className="lede">
        Everything Kivi has learned, in its own words, with the moments it learned
        it from. Correct or forget any of it in one line.
      </p>

      {Object.entries(data.groups || {}).map(([type, items]) => (
        <section className="knows-group" key={type}>
          <h3>{GROUP_TITLES[type] || type} / {items.length}</h3>
          {items.map((m) => <Memory key={m.id} m={m} onChanged={load} />)}
        </section>
      ))}

      {data.total === 0 && <div className="empty">Kivi has not learned anything yet.</div>}

      <div className="refused">
        <h3>Kivi chose not to remember</h3>
        <p>
          Health, money, relationship conflicts and how you were feeling are never
          turned into memory, even when you mention them in passing. Kivi records
          only that it declined, and when. Never what was said.
        </p>
        {(data.chose_not_to_remember || []).length === 0 && (
          <div className="cat"><span>nothing declined yet</span><span>-</span></div>
        )}
        {(data.chose_not_to_remember || []).map((c) => (
          <div className="cat" key={c.category}>
            <span>{c.category.replace(/_/g, ' ')}</span>
            <span>
              {c.count} time{c.count === 1 ? '' : 's'} - last {c.dates[c.dates.length - 1]}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
