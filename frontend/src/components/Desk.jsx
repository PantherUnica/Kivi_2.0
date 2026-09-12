import React, { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { api } from '../api.js'
import { useSpeech } from '../lib/speech.js'
import VoiceButton from './VoiceButton.jsx'

const APPS = ['Slack', 'Gmail', 'Notion', 'Linear', 'WhatsApp']

export default function Desk({ onOpenAsk }) {
  const [text, setText] = useState('')
  const [app, setApp] = useState('Slack')
  const [destination, setDestination] = useState('#halo-build')
  const [project, setProject] = useState('Halo')
  const [learned, setLearned] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [stream, setStream] = useState([])
  const [stats, setStats] = useState(null)

  const refresh = () => {
    api.interactions(18).then((d) => setStream(d.interactions)).catch(() => {})
    api.stats().then(setStats).catch(() => {})
  }
  useEffect(refresh, [])

  const capture = async (payload) => {
    setBusy(true); setError(null)
    try {
      const out = await api.dictate({ ...payload, app, destination, project })
      setLearned(out)
      setText('')
      refresh()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const send = () => text.trim() && capture({ text })

  // Spoken path: the recogniser's raw output goes to the backend as raw_asr
  // and Kivi writes it. The typed path sends `text` as already written.
  const speech = useSpeech({ onFinal: (raw) => capture({ raw_asr: raw }) })
  const heard = [speech.finalText, speech.interim].filter(Boolean)

  const activeMemories = (stats?.memories_by_type || [])
    .filter((m) => m.status === 'active')
    .reduce((n, m) => n + m.count, 0)
  const refused = (stats?.ignored_by_category || []).reduce((n, c) => n + c.count, 0)

  return (
    <div className="wide">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
      >
        <p className="eyebrow"><span className="dot">—</span> the desk</p>
        <h1 className="display">
          Speak.<br />Kivi <em>writes.</em>
        </h1>
        <p className="lede">
          Dictation is never rewritten by memory. Kivi only listens for what will
          still matter later, and tells you when it learned something.
        </p>
      </motion.div>

      <div className="desk-grid">
        <div>
          <div className="row" style={{ marginBottom: '1rem' }}>
            <VoiceButton speech={speech} size="lg" label="press and speak" />
          </div>

          {(speech.listening || heard.length > 0) && (
            <div className="interim">
              {speech.finalText && <span className="heard">{speech.finalText} </span>}
              {speech.interim && <span className="partial">{speech.interim}</span>}
              {speech.listening && !heard.length && <span className="partial">say something…</span>}
            </div>
          )}

          <textarea
            className="mic"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="…or type. Try: For Acme I always keep updates to three short paragraphs, no bullet points."
            style={{ marginTop: '0.8rem', minHeight: 100 }}
          />
          <div className="row" style={{ marginTop: '0.9rem' }}>
            <select className="field" value={app} onChange={(e) => setApp(e.target.value)}>
              {APPS.map((a) => <option key={a}>{a}</option>)}
            </select>
            <input
              className="field" value={destination} style={{ width: 150 }}
              onChange={(e) => setDestination(e.target.value)} placeholder="destination"
            />
            <input
              className="field" value={project} style={{ width: 110 }}
              onChange={(e) => setProject(e.target.value)} placeholder="project"
            />
            <button className="act" onClick={send} disabled={busy || !text.trim()}>
              {busy ? <span className="spin" /> : 'Capture'}
            </button>
          </div>

          {error && <div className="err">{error}</div>}

          {learned && (
            <motion.div
              className="learned"
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
            >
              <div>
                <strong>{learned.note || 'Kivi kept your words and learned nothing new.'}</strong>
                {learned.raw_asr && learned.raw_asr !== learned.text && (
                  <div className="raw-vs">
                    <span className="k">heard</span><span className="raw">{learned.raw_asr}</span>
                    <span className="k">wrote</span><span>{learned.text}</span>
                  </div>
                )}
                <div style={{ marginTop: '0.5rem', color: 'var(--paper-dim)' }}>
                  {learned.learned.created.map((m) => (
                    <div key={m.id}>+ {m.claim}</div>
                  ))}
                  {learned.learned.updated.map((m) => (
                    <div key={m.id}>↑ {m.claim} — now {m.corroborations} sightings</div>
                  ))}
                  {learned.learned.ignored.map((m, i) => (
                    <div key={i} className="dim">− ignored ({m.rule}): {m.why}</div>
                  ))}
                  {learned.learned.fenced.map((c, i) => (
                    <div key={i} className="dim">⌀ chose not to remember: {c.replace('_', ' ')}</div>
                  ))}
                </div>
              </div>
              <button onClick={() => setLearned(null)}>×</button>
            </motion.div>
          )}
        </div>

        <aside>
          {stats && (
            <div className="metrics" style={{ marginBottom: '2rem' }}>
              <div><strong>{stats.interactions}</strong>dictations kept</div>
              <div><strong>{activeMemories}</strong>things learned</div>
              <div><strong>{refused}</strong>refused</div>
            </div>
          )}
          <button className="ghost" onClick={onOpenAsk}>Ask Kivi about any of it →</button>
        </aside>
      </div>

      <h2 className="section">Recently</h2>
      <div className="stream">
        {stream.length === 0 && <div className="empty">Nothing captured yet.</div>}
        {stream.map((i) => (
          <div className="entry" key={i.id}>
            <div className="when">
              {new Date(i.captured_at).toLocaleDateString(undefined,
                { day: '2-digit', month: 'short' })}
              <br />
              {new Date(i.captured_at).toLocaleTimeString(undefined,
                { hour: '2-digit', minute: '2-digit' })}
            </div>
            <div>
              <div className="what">{i.text}</div>
              <div className="tags">
                {i.app && <span className="chip">{i.app}</span>}
                {i.destination && <span className="chip">{i.destination}</span>}
                {i.project && <span className="chip accent">{i.project}</span>}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
