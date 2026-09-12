import { useCallback, useEffect, useRef, useState } from 'react'

/*
  Browser speech recognition, wrapped as a hook.

  The brief says not to build ASR, and we haven't: this is the recogniser
  the browser already ships (Chrome / Edge / Safari). What it produces is
  RAW ASR - lowercased, unpunctuated, occasionally wrong - and it is sent to
  the backend as exactly that, so the raw / formatted distinction the whole
  memory system rests on stays real rather than simulated.

  Firefox has no Web Speech API; the hook reports `supported: false` and the
  UI falls back to typing.
*/

const SR = typeof window !== 'undefined'
  ? (window.SpeechRecognition || window.webkitSpeechRecognition)
  : null

export function useSpeech({ lang = 'en-IN', continuous = true, onFinal } = {}) {
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const [finalText, setFinalText] = useState('')
  const [error, setError] = useState(null)
  const [level, setLevel] = useState(0)          // crude "voice activity" for the UI
  const rec = useRef(null)
  const finalRef = useRef('')
  const audio = useRef(null)

  const supported = Boolean(SR)

  const stop = useCallback(() => {
    try { rec.current?.stop() } catch {}
    if (audio.current) {
      audio.current.stream.getTracks().forEach((t) => t.stop())
      cancelAnimationFrame(audio.current.raf)
      audio.current.ctx.close().catch(() => {})
      audio.current = null
    }
    setListening(false)
    setLevel(0)
  }, [])

  const start = useCallback(async () => {
    if (!SR) { setError('This browser has no speech recognition. Chrome or Edge do.'); return }
    setError(null)
    setInterim('')
    setFinalText('')
    finalRef.current = ''

    const r = new SR()
    r.lang = lang
    r.continuous = continuous
    r.interimResults = true
    r.maxAlternatives = 1

    r.onresult = (e) => {
      let interimNow = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript
        if (e.results[i].isFinal) finalRef.current += (finalRef.current ? ' ' : '') + t.trim()
        else interimNow += t
      }
      setFinalText(finalRef.current)
      setInterim(interimNow)
    }
    r.onerror = (e) => {
      if (e.error === 'no-speech') return
      setError(e.error === 'not-allowed'
        ? 'Microphone permission was refused.'
        : `Speech error: ${e.error}`)
      stop()
    }
    r.onend = () => {
      setListening(false)
      setLevel(0)
      const text = finalRef.current.trim()
      if (text && onFinal) onFinal(text)
    }

    rec.current = r
    r.start()
    setListening(true)

    // A small level meter from the mic, purely so the UI can show the bird
    // that it is hearing something. Fails silently if the browser refuses.
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const ctx = new (window.AudioContext || window.webkitAudioContext)()
      const src = ctx.createMediaStreamSource(stream)
      const an = ctx.createAnalyser()
      an.fftSize = 256
      src.connect(an)
      const buf = new Uint8Array(an.frequencyBinCount)
      const tick = () => {
        an.getByteTimeDomainData(buf)
        let sum = 0
        for (const v of buf) { const d = (v - 128) / 128; sum += d * d }
        setLevel(Math.min(1, Math.sqrt(sum / buf.length) * 4))
        audio.current.raf = requestAnimationFrame(tick)
      }
      audio.current = { stream, ctx, raf: 0 }
      audio.current.raf = requestAnimationFrame(tick)
    } catch {}
  }, [lang, continuous, onFinal, stop])

  useEffect(() => () => stop(), [stop])

  return { supported, listening, interim, finalText, error, level, start, stop }
}
