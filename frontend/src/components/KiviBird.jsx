import React, { useEffect, useState } from 'react'
import { motion } from 'framer-motion'

/*
  The Kivi bird, and the one story it tells on the landing page:

    walk in  ->  stop  ->  tilt head, listen  ->  the words get written

  Original artwork. A round body, a long curved beak (the listening part),
  two stubby legs. Everything is stroked SVG so it scales to any size and
  animates without an asset pipeline.

  Phases are driven by a timeline in the parent (via `phase`), so the
  headline can type out exactly when the bird finishes listening.
*/

const EASE = [0.22, 1, 0.36, 1]

export default function KiviBird({ phase = 'idle', size = 320 }) {
  const walking = phase === 'walk'
  const listening = phase === 'listen'
  const writing = phase === 'write'

  return (
    <motion.div
      style={{ width: size, height: size * 0.78, position: 'relative' }}
      initial={{ x: -size * 1.6, opacity: 0 }}
      animate={
        phase === 'idle'
          ? { x: -size * 1.6, opacity: 0 }
          : { x: 0, opacity: 1 }
      }
      transition={{ duration: 1.9, ease: EASE }}
    >
      {/* listening rings, only while listening */}
      {listening &&
        [0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="bird-ring"
            style={{ left: '78%', top: '22%' }}
            initial={{ scale: 0.4, opacity: 0.8 }}
            animate={{ scale: 2.6, opacity: 0 }}
            transition={{ duration: 1.6, repeat: Infinity, delay: i * 0.5, ease: 'easeOut' }}
          />
        ))}

      <svg viewBox="0 0 320 250" width="100%" height="100%" aria-hidden="true">
        <defs>
          <radialGradient id="kivi-body" cx="40%" cy="38%" r="68%">
            <stop offset="0%" stopColor="#c9ff7a" />
            <stop offset="55%" stopColor="#9dfb3f" />
            <stop offset="100%" stopColor="#5fbf0c" />
          </radialGradient>
          <filter id="kivi-glow" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="9" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* ground shadow */}
        <motion.ellipse
          cx="150" cy="238" rx="96" ry="9" fill="#000" opacity="0.45"
          animate={walking ? { rx: [96, 88, 96] } : { rx: 96 }}
          transition={{ duration: 0.5, repeat: walking ? Infinity : 0 }}
        />

        {/* legs - swing while walking */}
        <motion.g
          stroke="#6fd310" strokeWidth="9" strokeLinecap="round" fill="none"
          style={{ originX: '120px', originY: '190px' }}
          animate={walking ? { rotate: [14, -14, 14] } : { rotate: 0 }}
          transition={{ duration: 0.5, repeat: walking ? Infinity : 0, ease: 'easeInOut' }}
        >
          <path d="M120 188 L112 232 M112 232 L96 240 M112 232 L124 242" />
        </motion.g>
        <motion.g
          stroke="#6fd310" strokeWidth="9" strokeLinecap="round" fill="none"
          style={{ originX: '172px', originY: '190px' }}
          animate={walking ? { rotate: [-14, 14, -14] } : { rotate: 0 }}
          transition={{ duration: 0.5, repeat: walking ? Infinity : 0, ease: 'easeInOut' }}
        >
          <path d="M172 188 L178 232 M178 232 L162 240 M178 232 L192 242" />
        </motion.g>

        {/* body + head, bob while walking, tilt while listening */}
        <motion.g
          style={{ originX: '150px', originY: '150px' }}
          animate={
            walking
              ? { y: [0, -5, 0], rotate: 0 }
              : listening
                ? { y: 0, rotate: -7 }
                : { y: 0, rotate: 0 }
          }
          transition={
            walking
              ? { duration: 0.5, repeat: Infinity, ease: 'easeInOut' }
              : { duration: 0.8, ease: EASE }
          }
        >
          <ellipse cx="140" cy="140" rx="104" ry="86" fill="url(#kivi-body)" filter="url(#kivi-glow)" />
          {/* a hint of feather texture */}
          <path d="M70 120 q30 -22 60 0 M60 150 q30 -22 60 0 M78 178 q30 -22 60 0"
                stroke="#6fd310" strokeWidth="2.5" fill="none" opacity="0.5" />

          {/* head */}
          <circle cx="212" cy="86" r="44" fill="url(#kivi-body)" />
          {/* eye */}
          <circle cx="228" cy="76" r="6.5" fill="#0a0b09" />
          <circle cx="230" cy="74" r="2" fill="#f4f3ed" />

          {/* beak - the listening part */}
          <motion.path
            d="M248 88 C 280 84, 300 92, 318 104"
            stroke="#9dfb3f" strokeWidth="9" strokeLinecap="round" fill="none"
            animate={listening ? { opacity: [1, 0.55, 1] } : { opacity: 1 }}
            transition={{ duration: 1.1, repeat: listening ? Infinity : 0 }}
          />
          <path d="M248 88 C 280 84, 300 92, 318 104"
                stroke="#0a0b09" strokeWidth="2" strokeLinecap="round" fill="none" opacity="0.35" />
        </motion.g>

        {/* when writing: a pen-stroke flick from the beak toward the headline */}
        {writing && (
          <motion.path
            d="M318 104 C 335 110, 345 118, 360 128"
            stroke="#9dfb3f" strokeWidth="4" strokeLinecap="round" fill="none"
            initial={{ pathLength: 0, opacity: 0 }}
            animate={{ pathLength: 1, opacity: [0, 1, 0] }}
            transition={{ duration: 0.9, ease: 'easeOut' }}
          />
        )}
      </svg>
    </motion.div>
  )
}

/* Types a string out, one character at a time, when `go` becomes true. */
export function Typewriter({ text, go, speed = 55, onDone }) {
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!go) return
    let i = 0
    const t = setInterval(() => {
      i += 1
      setN(i)
      if (i >= text.length) {
        clearInterval(t)
        onDone && onDone()
      }
    }, speed)
    return () => clearInterval(t)
  }, [go, text, speed])
  return (
    <span>
      {text.slice(0, n)}
      <span className="caret" aria-hidden="true">|</span>
    </span>
  )
}
