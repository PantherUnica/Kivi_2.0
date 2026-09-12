import React, { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { api } from '../api.js'
import Trace from './Trace.jsx'

const SUGGESTIONS = [
  'What do you know about how I write for Acme?',
  'What did we decide about the Halo data layer?',
  'What did I promise Priya?',
  'How do I write emails to Northwind?',
  'What do you know about my health?',
  "What is Priya's manager called?",
]

function Citation({ cite }) {
  const [open, setOpen] = useState(false)
  const ev = cite.memory?.evidence || []
  const inter = cite.interaction

  return (
    <>
      <button
        className={`cite ${cite.used ? '' : 'unused'}`}
        onClick={() => setOpen((o) => !o)}
        title={cite.used ? 'used in the answer' : 'retrieved but not cited'}
      >
        [{cite.citation_id}] {cite.kind === 'memory' ? 'memory' : 'dictation'}
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            className="evidence"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
          >
            {cite.kind === 'memory' ? (
              <>
                <div className="quote">“{cite.memory?.claim}”</div>
                <div className="src">
                  {cite.memory?.type} · confidence {cite.memory?.confidence} ·
                  {' '}{cite.memory?.corroboration_count} sighting(s) ·
                  {' '}{cite.memory?.explicitness?.replace('_', ' ')}
                </div>
                {ev.map((e, i) => (
                  <div key={i} style={{ marginTop: '0.7rem' }}>
                    <div className="quote" style={{ fontSize: '0.9rem' }}>“{e.quote}”</div>
                    <div className="src">
                      {new Date(e.captured_at).toLocaleString()} · {e.app} · {e.destination}
                    </div>
                  </div>
                ))}
              </>
            ) : (
              <>
                <div className="quote">“{inter?.text || cite.text}”</div>
                <div className="src">
                  {inter && new Date(inter.captured_at).toLocaleString()} ·
                  {' '}{inter?.app} · {inter?.destination}
                </div>
              </>
            )}
            <div className="src" style={{ marginTop: '0.6rem' }}>
              retrieval score {cite.score?.toFixed(4)} ·
              {' '}channels {(cite.breakdown?.channels || []).join(', ')}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}

export default function Ask() {
  const [q, setQ] = useState('')
  const [turns, setTurns] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const ask = async (utterance) => {
    const text = (utterance ?? q).trim()
    if (!text) return
    setBusy(true); setError(null); setQ('')
    try {
      const out = await api.ask({ utterance: text })
      setTurns((t) => [{ utterance: text, ...out }, ...t])
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="wide">
      <p className="eyebrow"><span className="dot">—</span> hey kivi</p>
      <h1 className="display">Ask it <em>anything</em><br />it actually heard.</h1>
      <p className="lede">
        Every answer carries the dictations it came from. When your history doesn't
        contain the answer, Kivi says so instead of inventing one.
      </p>

      <div className="ask-bar">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && ask()}
          placeholder="Hey Kivi..."
        />
        <button className="act" onClick={() => ask()} disabled={busy}>
          {busy ? <span className="spin" /> : 'Ask'}
        </button>
      </div>

      <div className="suggest">
        {SUGGESTIONS.map((s) => (
          <button key={s} onClick={() => ask(s)}>{s}</button>
        ))}
      </div>

      {error && <div className="err">{error}</div>}

      <AnimatePresence>
        {turns.map((t, idx) => (
          <motion.div
            key={t.turn_id || idx}
            className={`answer ${t.support_status || 'grounded'}`}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="you">you asked</div>
            <div className="said">
              {t.answer || t.message || t.attribution || t.text || '—'}
            </div>

            <div className="status-line">
              <span className={`badge ${t.support_status || 'grounded'}`}>
                {t.support_status || 'grounded'}
              </span>
              {t.confidence > 0 && <span>confidence {t.confidence}</span>}
              <span>{t.metrics?.retrieval_ms ?? 0} ms retrieval</span>
              <span>{t.elapsed_ms} ms total</span>
              {t.intent?.intent && <span>intent: {t.intent.intent}</span>}
            </div>

            {t.abstained && (
              <p className="dim" style={{ fontSize: '0.85rem', maxWidth: '52ch' }}>
                {t.notes?.[0] || 'Nothing in your history supports an answer here.'}
              </p>
            )}

            {t.overridden?.length > 0 && (
              <div className="overridden">
                Your instruction overrode {t.overridden.length} stored style memory:
                {t.overridden.map((o) => <div key={o.id}><s>{o.claim}</s></div>)}
              </div>
            )}

            {t.attribution && t.text && (
              <div className="learned" style={{ marginBottom: '1rem' }}>
                <div><strong>{t.attribution}</strong></div>
              </div>
            )}

            {t.citations?.length > 0 && (
              <div style={{ marginBottom: '0.6rem' }}>
                {t.citations.map((c) => <Citation key={c.citation_id} cite={c} />)}
              </div>
            )}

            {t.results?.length > 0 && (
              <div className="stream" style={{ marginTop: '1rem' }}>
                {t.results.slice(0, 6).map((r) => (
                  <div className="entry" key={r.interaction_id}>
                    <div className="when">
                      {new Date(r.captured_at).toLocaleDateString(undefined,
                        { day: '2-digit', month: 'short' })}
                    </div>
                    <div>
                      <div className="what">{r.text}</div>
                      <div className="tags">
                        <span className="chip">{r.app}</span>
                        <span className="chip">{r.destination}</span>
                        <span className="chip quiet">score {r.score?.toFixed(3)}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {t.turn_id && <Trace turnId={t.turn_id} excluded={t.excluded} />}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}
