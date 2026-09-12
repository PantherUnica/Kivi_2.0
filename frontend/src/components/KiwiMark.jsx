// The Kivi mark. One stroke, one body, one beak - the beak doubles as the
// listening indicator, which is the only animation in the identity.
export default function KiwiMark({ listening = false, size = 26 }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
      <defs>
        <linearGradient id="kivi-body" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#6fd310" />
          <stop offset="100%" stopColor="#c9ff7a" />
        </linearGradient>
      </defs>
      <ellipse cx="27" cy="33" rx="19" ry="20" fill="url(#kivi-body)" />
      <circle cx="35" cy="24" r="2.1" fill="#0a0b09" />
      <path
        d="M42 26 L62 19"
        stroke="#9dfb3f"
        strokeWidth="3.2"
        strokeLinecap="round"
        opacity={listening ? 1 : 0.9}
      >
        {listening && (
          <animate
            attributeName="opacity"
            values="0.35;1;0.35"
            dur="1.3s"
            repeatCount="indefinite"
          />
        )}
      </path>
      <path d="M20 52 L18 60 M32 52 L34 60" stroke="#6fd310" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}
