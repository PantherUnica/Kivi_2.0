import React, { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import KiwiMark from './components/KiwiMark.jsx'
import Desk from './components/Desk.jsx'
import Ask from './components/Ask.jsx'
import Knows from './components/Knows.jsx'
import { api } from './api.js'

const VIEWS = [
  ['desk', 'Desk'],
  ['ask', 'Hey Kivi'],
  ['knows', 'What Kivi knows'],
]

export default function App() {
  const [view, setView] = useState('desk')
  const [health, setHealth] = useState(null)

  useEffect(() => { api.health().then(setHealth).catch(() => {}) }, [])

  return (
    <div className="shell">
      <header className="masthead">
        <div className="wordmark">
          <KiwiMark listening={view === 'ask'} />
          kivi
        </div>
        <nav className="nav">
          {VIEWS.map(([key, label]) => (
            <button
              key={key}
              className={view === key ? 'on' : ''}
              onClick={() => setView(key)}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>

      <main>
        <AnimatePresence mode="wait">
          <motion.div
            key={view}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
          >
            {view === 'desk' && <Desk onOpenAsk={() => setView('ask')} />}
            {view === 'ask' && <Ask />}
            {view === 'knows' && <Knows />}
          </motion.div>
        </AnimatePresence>
      </main>

      {health && (
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
