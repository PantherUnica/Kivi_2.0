import React, { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import KiwiMark from './components/KiwiMark.jsx'
import Landing from './components/Landing.jsx'
import Desk from './components/Desk.jsx'
import Ask from './components/Ask.jsx'
import Knows from './components/Knows.jsx'
import { api } from './api.js'
import './landing.css'

const VIEWS = [
  ['desk', 'Desk'],
  ['ask', 'Hey Kivi'],
  ['knows', 'What Kivi knows'],
]

/* `#app`, `#ask`, `#knows` deep-link straight past the landing page. */
function initialView() {
  const h = (window.location.hash || '').replace('#', '')
  if (h === 'app' || h === 'desk') return 'desk'
  if (h === 'ask' || h === 'knows') return h
  return 'landing'
}

export default function App() {
  const [view, setView] = useState(initialView)
  const [health, setHealth] = useState(null)

  useEffect(() => { api.health().then(setHealth).catch(() => {}) }, [])

  const go = (v) => {
    setView(v)
    window.history.replaceState(null, '', v === 'landing' ? '#' : `#${v}`)
    window.scrollTo({ top: 0 })
  }

  const onLanding = view === 'landing'

  return (
    <div className="shell">
      <header className="masthead">
        <button className="wordmark linkish-mark" onClick={() => go('landing')}>
          <KiwiMark listening={view === 'ask'} />
          kivi
        </button>
        <nav className="nav">
          {onLanding ? (
            <button className="bracket-btn" onClick={() => go('desk')}>[ open kivi ]</button>
          ) : (
            VIEWS.map(([key, label]) => (
              <button key={key} className={view === key ? 'on' : ''} onClick={() => go(key)}>
                {label}
              </button>
            ))
          )}
        </nav>
      </header>

      <main className={onLanding ? 'is-landing' : ''}>
        <AnimatePresence mode="wait">
          <motion.div
            key={view}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
          >
            {view === 'landing' && <Landing onEnter={() => go('desk')} />}
            {view === 'desk' && <Desk onOpenAsk={() => go('ask')} />}
            {view === 'ask' && <Ask />}
            {view === 'knows' && <Knows />}
          </motion.div>
        </AnimatePresence>
      </main>

      {health && !onLanding && (
        <footer
          className="mono dim"
          style={{
            padding: '1.2rem clamp(1.5rem, 5vw, 5rem)',
            borderTop: '1px solid var(--ink-line)',
            display: 'flex',
            gap: '1.5rem',
            flexWrap: 'wrap',
          }}
        >
          <span>llm: {health.llm_provider} / {health.llm_model}</span>
          <span>
            embeddings: {health.embedding_provider} @ {health.embed_dim}d
            {health.embedding_degraded ? '  (DEGRADED: lexical only)' : ''}
          </span>
          <span>{health.counts?.interactions ?? 0} dictations</span>
          <span>{health.counts?.memories ?? 0} memories</span>
          <span>{health.counts?.ignored_spans ?? 0} refused</span>
        </footer>
      )}
    </div>
  )
}
