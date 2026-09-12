import React, { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'
import Lenis from 'lenis'
import KiviBird, { Typewriter } from './KiviBird.jsx'
import { api } from '../api.js'
import { useSpeech } from '../lib/speech.js'
import VoiceButton from './VoiceButton.jsx'

gsap.registerPlugin(ScrollTrigger)

const EASE = [0.22, 1, 0.36, 1]

/* What Kivi remembers - the five types, in the survey's own order. */
const REMEMBERS = [
  ['01', 'How you write', 'for each client, each channel. Learned from repetition, never from one remark.', '81% ranked it first'],
  ['02', 'What you decided', 'and when. If you reverse it, the old decision is demoted, not deleted.', '75%'],
  ['03', 'How you work', 'the Monday update, the standup notes, the things you do every week.', '65%'],
  ['04', 'What you promised', 'with the due date you said out loud. It expires when the date does.', '80% of professionals'],
  ['05', 'What you are working on', 'which project belongs to which client. Scaffolding, not surveillance.', '59%'],
]

const REFUSES = [
  ['Health', 'a doctor, a scan, a bad night'],
  ['Money', 'salary, a loan, the EMI'],
  ['Relationships', 'an argument, a falling-out'],
  ['How you were feeling', 'anxious, burnt out, low'],
]

const MARQUEE = [
  'remembers what you said', 'not who you are', 'every answer carries its source',
  'says "I don\'t know" out loud', 'forgets when you ask', 'never rewrites your dictation',
]

export default function Landing({ onEnter }) {
  const [phase, setPhase] = useState('idle')
  const [typed, setTyped] = useState(false)
  const [stats, setStats] = useState(null)
  const [spoken, setSpoken] = useState(null)   // { raw, text } after you speak to the bird
  const root = useRef(null)

  // Speak to the bird. It listens while you talk, then writes what you said -
  // through the real dictation endpoint, so the line on screen is the same
  // one that just landed in the database.
  const speech = useSpeech({
    onFinal: async (raw) => {
      setPhase('write')
      try {
        const out = await api.dictate({ raw_asr: raw, app: 'Kivi', destination: 'landing' })
        setSpoken({ raw, text: out.text, note: out.note })
        api.stats().then(setStats).catch(() => {})
      } catch {
        setSpoken({ raw, text: raw, note: null })
      }
    },
  })
  useEffect(() => {
    if (speech.listening) { setPhase('listen'); setSpoken(null) }
  }, [speech.listening])

  /* ---- the bird's performance ------------------------------------------ */
  useEffect(() => {
    const t1 = setTimeout(() => setPhase('walk'), 300)
    const t2 = setTimeout(() => setPhase('listen'), 2400)
    const t3 = setTimeout(() => setPhase('write'), 4300)
    return () => [t1, t2, t3].forEach(clearTimeout)
  }, [])

  /* ---- live numbers, if the backend is up ------------------------------ */
  useEffect(() => { api.stats().then(setStats).catch(() => {}) }, [])

  /* ---- smooth scroll + reveals ----------------------------------------- */
  useEffect(() => {
    const lenis = new Lenis({ lerp: 0.09, smoothWheel: true })
    const raf = (t) => { lenis.raf(t); requestAnimationFrame(raf) }
    const id = requestAnimationFrame(raf)
    lenis.on('scroll', ScrollTrigger.update)

    const ctx = gsap.context(() => {
      gsap.utils.toArray('.reveal').forEach((el) => {
        gsap.fromTo(
          el,
          { y: 36, opacity: 0 },
          {
            y: 0, opacity: 1, duration: 1.1, ease: 'power3.out',
            scrollTrigger: { trigger: el, start: 'top 84%', once: true },
          }
        )
      })
      gsap.utils.toArray('.line-reveal').forEach((el) => {
        gsap.fromTo(el, { scaleX: 0 }, {
          scaleX: 1, duration: 1.2, ease: 'power3.inOut', transformOrigin: 'left',
          scrollTrigger: { trigger: el, start: 'top 90%', once: true },
        })
      })
    }, root)

    return () => { ctx.revert(); lenis.destroy(); cancelAnimationFrame(id) }
  }, [])

  const learned = (stats?.memories_by_type || [])
    .filter((m) => m.status === 'active').reduce((n, m) => n + m.count, 0)
  const refused = (stats?.ignored_by_category || []).reduce((n, c) => n + c.count, 0)

  return (
    <div className="landing" ref={root}>

      {/* ------------------------------------------------------------ hero */}
      <section className="hero">
        <motion.p
          className="bracket"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.4, duration: 1 }}
        >
          [ voice-first &nbsp;·&nbsp; remembers what you said &nbsp;·&nbsp; 2026 ]
        </motion.p>

        <div className="hero-stage">
          <div className="hero-bird">
            <KiviBird phase={phase} size={340} />
          </div>

          <h1 className="hero-title">
            <motion.span
              className="line"
              initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.6, duration: 1, ease: EASE }}
            >
              Speak.
            </motion.span>
            <span className="line acid">
              {speech.listening ? (
                <span className="you-said">
                  {(speech.finalText + ' ' + speech.interim).trim() || '…'}
                  <span className="caret">|</span>
                </span>
              ) : spoken ? (
                <Typewriter key={spoken.text} text={spoken.text} go speed={38} />
              ) : (
                <Typewriter text="Kivi writes." go={phase === 'write'} onDone={() => setTyped(true)} />
              )}
            </span>
          </h1>
        </div>

        <motion.div
          className="hero-foot"
          initial={{ opacity: 0 }} animate={{ opacity: typed ? 1 : 0 }} transition={{ duration: 0.9 }}
        >
          <p className="lede">
            A voice-first assistant that remembers what you said and how you say it.
            Never who you are.
          </p>
          <div className="hero-cta">
            <VoiceButton speech={speech} size="lg" label="press and speak to it" />
            <button className="act big" onClick={onEnter}>Open Kivi</button>
            <a className="ghost-link" href="#remembers">[ what it remembers ]</a>
          </div>
          {spoken && (
            <motion.div className="raw-vs" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <span className="k">heard</span><span className="raw">{spoken.raw}</span>
              <span className="k">wrote</span><span>{spoken.text}</span>
              <span className="k">kept</span>
              <span>{spoken.note || 'Yes — the words, verbatim. Nothing new learned from them.'}</span>
            </motion.div>
          )}
        </motion.div>

        <motion.div
          className="scroll-hint"
          initial={{ opacity: 0 }} animate={{ opacity: typed ? 1 : 0 }} transition={{ delay: 0.6 }}
        >
          <span className="tick" /> scroll
        </motion.div>
      </section>

      {/* --------------------------------------------------------- marquee */}
      <div className="marquee" aria-hidden="true">
        <div className="marquee-track">
          {[...MARQUEE, ...MARQUEE].map((t, i) => (
            <span key={i}>{t}<em>◦</em></span>
          ))}
        </div>
      </div>

      {/* ------------------------------------------------------- mission */}
      <section className="block">
        <p className="bracket reveal">[ the idea ]</p>
        <h2 className="statement reveal">
          Most memory products try to know <em>more</em> about you.
          Kivi tries to know a <em>narrow</em> set of things well enough
          that you stop repeating yourself, and never feel watched.
        </h2>
        <div className="line-reveal rule" />
        <p className="small reveal">
          Built to the stricter line our 100-person survey drew: professionals wanted to
          <strong> see</strong> what is stored (87%), students wanted to
          <strong> delete</strong> it (79%), and both put a person's relationships dead last.
        </p>
      </section>

      {/* ------------------------------------------------------ remembers */}
      <section className="block" id="remembers">
        <p className="bracket reveal">[ what it remembers ]</p>
        <ol className="numbered">
          {REMEMBERS.map(([n, title, body, stat]) => (
            <li className="reveal" key={n}>
              <span className="num">{n}</span>
              <div>
                <h3>{title}</h3>
                <p>{body}</p>
              </div>
              <span className="stat">{stat}</span>
            </li>
          ))}
        </ol>
      </section>

      {/* -------------------------------------------------------- refuses */}
      <section className="block refuses">
        <p className="bracket reveal">[ what it refuses ]</p>
        <h2 className="statement reveal">
          Four things never become memory,<br />even when you say them in passing.
        </h2>
        <div className="refuse-grid">
          {REFUSES.map(([k, v]) => (
            <div className="refuse reveal" key={k}>
              <span className="x">⌀</span>
              <div>
                <strong>{k}</strong>
                <span>{v}</span>
              </div>
            </div>
          ))}
        </div>
        <p className="small reveal">
          Kivi keeps only the fact that it declined, and when. Never what was said.
          You can see the refusals; nobody can read them.
        </p>
      </section>

      {/* ------------------------------------------------------- numbers */}
      <section className="block numbers">
        <p className="bracket reveal">[ right now, in this instance ]</p>
        <div className="num-row">
          <div className="reveal">
            <strong>{stats ? stats.interactions : '—'}</strong>
            <span>dictations kept, verbatim</span>
          </div>
          <div className="reveal">
            <strong>{stats ? learned : '—'}</strong>
            <span>things it chose to remember</span>
          </div>
          <div className="reveal">
            <strong>{stats ? refused : '—'}</strong>
            <span>times it chose not to</span>
          </div>
        </div>
        <p className="small reveal">
          Those are live counts from the database behind this page, not copy.
        </p>
      </section>

      {/* -------------------------------------------------------- the loop */}
      <section className="block cta">
        <div className="line-reveal rule" />
        <h2 className="cta-title reveal">
          Ask it anything<br />it <em>actually</em> heard.
        </h2>
        <p className="small reveal">
          Every answer carries the dictation it came from. When the answer isn't in
          your history, Kivi says so instead of inventing one.
        </p>
        <div className="reveal">
          <button className="act big" onClick={onEnter}>Open Kivi</button>
        </div>
      </section>

      {/* --------------------------------------------------------- footer */}
      <footer className="land-foot">
        <div>
          <span className="bracket">[ kivi &nbsp;·&nbsp; semantic memory ]</span>
        </div>
        <div className="foot-links">
          <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer">api</a>
          <span>·</span>
          <a href="#remembers">what it remembers</a>
          <span>·</span>
          <button className="linkish" onClick={onEnter}>open kivi</button>
        </div>
        <div className="bracket dim">[ the words stay yours ]</div>
      </footer>
    </div>
  )
}
