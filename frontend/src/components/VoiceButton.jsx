import React from 'react'
import { motion } from 'framer-motion'

/*
  Press to talk. One control, three surfaces.
  `level` (0..1) drives the bars so it is visibly hearing you.
*/
export default function VoiceButton({ speech, size = 'md', label = 'Speak' }) {
  const { supported, listening, level, start, stop, error } = speech

  if (!supported) {
    return (
      <span className="voice-unsupported" title="Speech recognition needs Chrome, Edge or Safari">
        [ no mic in this browser — type instead ]
      </span>
    )
  }

  return (
    <span className={`voice ${size}`}>
      <motion.button
        type="button"
        className={`voice-btn ${listening ? 'live' : ''}`}
        onClick={listening ? stop : start}
        whileTap={{ scale: 0.94 }}
        aria-pressed={listening}
        title={listening ? 'Stop' : 'Speak'}
      >
        {listening ? (
          <span className="bars" aria-hidden="true">
            {[0, 1, 2, 3, 4].map((i) => (
              <span
                key={i}
                style={{
                  height: `${18 + level * 100 * (0.5 + Math.abs(2 - i) * 0.25)}%`,
                  animationDelay: `${i * 90}ms`,
                }}
              />
            ))}
          </span>
        ) : (
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor" />
            <path d="M5 11a7 7 0 0 0 14 0M12 18v3" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" />
          </svg>
        )}
      </motion.button>
      <span className="voice-label">{listening ? 'listening… tap to stop' : label}</span>
      {error && <span className="voice-err">{error}</span>}
    </span>
  )
}
