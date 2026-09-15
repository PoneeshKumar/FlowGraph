/** Precision & recall vs cutoff — two lines on a 300×120 SVG; a marker at `cutoff`. */
export default function PrCurve({ curve, cutoff }) {
  if (!curve?.loaded) return null
  const W = 300, H = 120, P = 6
  const xs = curve.cutoffs, x0 = xs[0], x1 = xs[xs.length - 1]
  const X = (c) => P + ((c - x0) / (x1 - x0)) * (W - 2 * P)
  const Y = (v) => H - P - v * (H - 2 * P)
  const path = (arr) => arr.map((v, i) => `${i ? 'L' : 'M'}${X(xs[i]).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-[120px] w-full" role="img" aria-label="Precision and recall by cutoff">
      <path d={path(curve.precision)} fill="none" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      <path d={path(curve.recall)} fill="none" stroke="var(--high)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      <line x1={X(cutoff)} x2={X(cutoff)} y1={P} y2={H - P} stroke="var(--ink-4)" strokeDasharray="3 4" />
    </svg>
  )
}
