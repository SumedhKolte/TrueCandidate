/**
 * TrueCandidate mark + wordmark, redrawn with light strokes/text so it reads
 * on the app's dark-navy surfaces (the source lockup uses near-black #0A1128
 * strokes, which disappear against our #070d1a page background). The amber
 * gradient circle is unchanged — it's the one fixed brand color.
 */
export default function Logo({ className = "h-8", markOnly = false }) {
  if (markOnly) {
    return (
      <svg viewBox="0 0 150 150" className={className} aria-hidden="true">
        <defs>
          <linearGradient id="tcMarkGrad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#FFC400" />
            <stop offset="100%" stopColor="#F59E0B" />
          </linearGradient>
        </defs>
        <path d="M 40 45 L 40 25 L 60 25" fill="none" stroke="#f4f6fb" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M 110 45 L 110 25 L 90 25" fill="none" stroke="#f4f6fb" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M 40 105 L 40 125 L 60 125" fill="none" stroke="#f4f6fb" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M 110 105 L 110 125 L 90 125" fill="none" stroke="#f4f6fb" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="75" cy="75" r="22" fill="url(#tcMarkGrad)" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 650 150" className={className} role="img" aria-label="TrueCandidate">
      <defs>
        <linearGradient id="tcGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#FFC400" />
          <stop offset="100%" stopColor="#F59E0B" />
        </linearGradient>
      </defs>
      <g transform="translate(10, 0)">
        <path d="M 40 45 L 40 25 L 60 25" fill="none" stroke="#f4f6fb" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M 110 45 L 110 25 L 90 25" fill="none" stroke="#f4f6fb" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M 40 105 L 40 125 L 60 125" fill="none" stroke="#f4f6fb" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M 110 105 L 110 125 L 90 125" fill="none" stroke="#f4f6fb" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="75" cy="75" r="22" fill="url(#tcGrad)" />
        <text x="160" y="95" fontFamily="system-ui, -apple-system, sans-serif" fontSize="68" fontWeight="800" fill="#f4f6fb" letterSpacing="-1.5">True</text>
        <text x="315" y="95" fontFamily="system-ui, -apple-system, sans-serif" fontSize="68" fontWeight="300" fill="#b9c2d4" letterSpacing="-1">Candidate</text>
      </g>
    </svg>
  );
}
