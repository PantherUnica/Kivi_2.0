import React, { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { api } from '../api.js'

// "Why did Kivi say this?" - reachable from any answer, never in the way.
// The engineer surface the brief asks for, inside the product rather than in
// a separate console.
export default function Trace({ turnId }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  const toggle = async () => {
    const next = !open
    setOpen(next)
    if (next && !data) {
      try { setData(await api.trace(turnId)) } catch (e) { setError(e.message) }
    }
  }

  return (
    <div>
      <button className="ghost" onClick={toggle}>
        {open ? '- why did Kivi say this?' : '+ why did Kivi say this?'}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            className="trace"
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
          >
            {error && <div className="err">{error}</div>}
            {!data && !error && <span className="spin" />}

            {data && (
              <>
                <div className="metrics" style={{ marginBottom: '1.2rem' }}>
                  <div><strong>{data.turn.retrieval_ms} ms</strong>retrieval</div>
                  <div><strong>{data.turn.total_ms} ms</strong>end to end</div>
                  <div><strong>{data.candidates.length}</strong>candidates</div>
                  <div><strong>{data.candidates.filter((c) => c.included).length}</strong>used</div>
                  <div>
                    <strong>{data.turn.prompt_tokens + data.turn.completion_tokens}</strong>
                    tokens
                  </div>
                  <div><strong>${data.turn.cost_usd?.toFixed(6)}</strong>cost</div>
                </div>

                <table>
                  <thead>
                    <tr>
                      <th>kind</th><th>channels</th><th>rrf</th><th>final</th>
                      <th>breakdown</th><th>in</th><th>excluded because</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.candidates.slice(0, 24).map((c, i) => (
                      <tr key={i} className={c.included ? '' : 'out'}>
                        <td>{c.kind}</td>
                        <td>{c.channel}</td>
                        <td>{c.rrf?.toFixed(4)}</td>
                        <td>{c.final?.toFixed(4)}</td>
                        <td>
                          conf {c.breakdown?.confidence} - rec {c.breakdown?.recency} -
                          scope {c.breakdown?.scope_match} - {c.breakdown?.status}
                        </td>
                        <td>{c.included ? 'yes' : '-'}</td>
                        <td className="why">{c.exclusion_reason || ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                <p className="dim" style={{ marginTop: '1rem' }}>
                  Rows marked with a dash were retrieved and then deliberately left
                  out. That is the answer to "why did memory NOT affect this?"
                </p>
              </>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
