import { useState, useEffect, useMemo, useRef } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { METRICS, RECENT_ALERTS, RECENT_TRANSACTIONS, VOLUME_SERIES } from '../data/mockData'
import { RiskChip, StatusChip, RISK_VAR, useCountUp, useTweenValue } from './ui'

const DIGITS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]

function RollDigit({ digit }) {
  return (
    <span
      className="inline-block overflow-hidden tabular-nums leading-none"
      style={{ width: '0.58em', height: '1em', verticalAlign: 'baseline' }}
    >
      <motion.span
        className="block leading-none"
        animate={{ y: `calc(${digit} * -1em)` }}
        transition={{ duration: 0.1, ease: 'easeOut' }}
      >
        {DIGITS.map(d => (
          <span key={d} className="block h-[1em] leading-none">
            {d}
          </span>
        ))}
      </motion.span>
    </span>
  )
}

function RollingNumber({ value, format, className }) {
  const tweened = useTweenValue(value, 200)
  const text = format(tweened)
  return (
    <span className={`inline-flex items-center leading-none ${className ?? ''}`} aria-live="polite">
      {[...text].map((ch, i) => (
        /\d/.test(ch)
          ? <RollDigit key={i} digit={Number(ch)} />
          : <span key={i} className="inline-block leading-none">{ch}</span>
      ))}
    </span>
  )
}

const PERIODS = [
  { id: '24h', label: '24H' },
  { id: '7d',  label: '7D'  },
  { id: '30d', label: '30D' },
  { id: '90d', label: '90D' },
]

const SNAP_MINUTES_24H = 30

function lerp(a, b, t) {
  return a + (b - a) * t
}

function formatTimeLabel(totalMinutes) {
  const h24 = Math.floor(totalMinutes / 60) % 24
  const m = totalMinutes % 60
  const h12 = h24 % 12 || 12
  const suffix = h24 < 12 ? 'a' : 'p'
  return `${h12}:${String(m).padStart(2, '0')}${suffix}`
}

function build24hAxisMarks() {
  const marks = []
  const dayMinutes = 24 * 60
  for (let m = 0; m < dayMinutes; m += SNAP_MINUTES_24H) {
    marks.push({
      pct: m / dayMinutes,
      major: m % 60 === 0,
    })
  }
  marks.push({ pct: 1, major: true })
  return marks
}

const CHART_H = 220

function indexLeftPct(i, count) {
  return count <= 1 ? 0 : (i / (count - 1)) * 100
}

function ptTopPct(y) {
  return (y / CHART_H) * 100
}

function sampleSeriesAtFrac(base, frac) {
  const last = base.volume.length - 1
  const fIdx = frac * last
  const i = Math.min(Math.floor(fIdx), last - 1)
  const t = fIdx - i
  return {
    volume: lerp(base.volume[i], base.volume[i + 1], t),
    baseline: lerp(base.baseline[i], base.baseline[i + 1], t),
    txns: Math.round(lerp(base.txns[i], base.txns[i + 1], t)),
  }
}

function buildDense24hSeries(base) {
  const last = base.volume.length - 1
  const labels = []
  const volume = []
  const baseline = []
  const txns = []
  const dayMinutes = 24 * 60

  for (let m = 0; m < dayMinutes; m += SNAP_MINUTES_24H) {
    const frac = m / dayMinutes
    const sample = sampleSeriesAtFrac(base, frac)
    labels.push(formatTimeLabel(m))
    volume.push(sample.volume)
    baseline.push(sample.baseline)
    txns.push(sample.txns)
  }

  labels.push('Now')
  volume.push(base.volume[last])
  baseline.push(base.baseline[last])
  txns.push(base.txns[last])

  return { labels, volume, baseline, txns }
}

function tickIndices(count, maxTicks = 12) {
  if (count <= maxTicks) return [...Array(count).keys()]
  return Array.from({ length: maxTicks }, (_, t) =>
    Math.round(t * (count - 1) / (maxTicks - 1)),
  )
}

const STAT_CARDS = [
  { key: 'activeAccounts', label: 'Active accounts', format: 'count' },
  { key: 'cyclesDetected', label: 'Cycles detected', format: 'count', tone: 'text-critical' },
  { key: 'riskAlerts',     label: 'Open alerts',     format: 'count', tone: 'text-high' },
]

function buildPath(values, width, height, padY = 8) {
  const min = Math.min(...values) * 0.98
  const max = Math.max(...values) * 1.02
  const range = max - min || 1
  const step = width / (values.length - 1)

  const pts = values.map((v, i) => {
    const x = i * step
    const y = height - padY - ((v - min) / range) * (height - padY * 2)
    return { x, y, value: v }
  })

  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  const area = `${line} L${width},${height} L0,${height} Z`
  return { line, area, pts }
}

function fmtVolumeM(v) {
  return `$${v.toFixed(2)}M`
}

function buildRangeInfo(series, startIdx, endIdx) {
  const lo = Math.min(startIdx, endIdx)
  const hi = Math.max(startIdx, endIdx)
  const startVol = series.volume[lo]
  const endVol = series.volume[hi]
  let txnsTotal = 0
  for (let i = lo; i <= hi; i++) txnsTotal += series.txns[i]
  return {
    startIdx: lo,
    endIdx: hi,
    startLabel: series.labels[lo],
    endLabel: series.labels[hi],
    startVolume: startVol,
    endVolume: endVol,
    pctChange: startVol ? ((endVol - startVol) / startVol) * 100 : 0,
    volumeDelta: endVol - startVol,
    txnsTotal,
  }
}

function VolumeChart({ period, onHover, onSelect }) {
  const baseSeries = VOLUME_SERIES[period]
  const series = useMemo(() => {
    if (period !== '24h') return baseSeries
    return buildDense24hSeries(baseSeries)
  }, [period, baseSeries])

  const chartRef = useRef(null)
  const dragActiveRef = useRef(false)
  const seriesRef = useRef(series)
  const onSelectRef = useRef(onSelect)
  const onHoverRef = useRef(onHover)
  seriesRef.current = series
  onSelectRef.current = onSelect
  onHoverRef.current = onHover

  const [hoverIndex, setHoverIndex] = useState(null)
  const [drag, setDrag] = useState(null)

  const count = series.volume.length

  const indexFromClientX = (clientX) => {
    const el = chartRef.current
    if (!el) return 0
    const n = seriesRef.current.volume.length
    const rect = el.getBoundingClientRect()
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    return Math.round(ratio * (n - 1))
  }

  const emitSelect = (range) => {
    const s = seriesRef.current
    const cb = onSelectRef.current
    cb?.(range ? buildRangeInfo(s, range.start, range.end) : null)
  }

  const volumeGeom = useMemo(
    () => buildPath(series.volume, 800, 220),
    [series.volume],
  )
  const baselineGeom = useMemo(
    () => buildPath(series.baseline, 800, 220),
    [series.baseline],
  )

  const activeRange = drag
  const rangeLo = activeRange ? Math.min(activeRange.start, activeRange.end) : null
  const rangeHi = activeRange ? Math.max(activeRange.start, activeRange.end) : null
  const hasSelection = rangeLo != null && rangeHi != null && rangeHi - rangeLo >= 1

  const setHoverAtIndex = (idx) => {
    const s = seriesRef.current
    setHoverIndex(idx)
    onHoverRef.current?.({
      label: s.labels[idx],
      volume: s.volume[idx],
      baseline: s.baseline[idx],
      txns: s.txns[idx],
    })
  }

  const handleMove = (e) => {
    if (drag) return
    setHoverAtIndex(indexFromClientX(e.clientX))
  }

  const handleLeave = () => {
    if (drag) return
    setHoverIndex(null)
    onHoverRef.current?.(null)
  }

  const handlePointerDown = (e) => {
    if (e.button !== 0) return
    const idx = indexFromClientX(e.clientX)
    dragActiveRef.current = true
    setDrag({ start: idx, end: idx })
    emitSelect(null)
    setHoverIndex(null)
    onHoverRef.current?.(null)
  }

  useEffect(() => {
    const handlePointerMove = (e) => {
      if (!dragActiveRef.current) return
      const idx = indexFromClientX(e.clientX)
      setDrag(prev => {
        if (!prev) return prev
        return { start: prev.start, end: idx }
      })
    }

    const finishDrag = (e) => {
      if (!dragActiveRef.current) return
      dragActiveRef.current = false
      setDrag(null)
      emitSelect(null)

      const el = chartRef.current
      if (!el || e.clientX == null) return
      const rect = el.getBoundingClientRect()
      if (
        e.clientX >= rect.left && e.clientX <= rect.right
        && e.clientY >= rect.top && e.clientY <= rect.bottom
      ) {
        setHoverAtIndex(indexFromClientX(e.clientX))
      }
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', finishDrag)
    window.addEventListener('pointercancel', finishDrag)
    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', finishDrag)
      window.removeEventListener('pointercancel', finishDrag)
    }
  }, [])

  useEffect(() => {
    if (!drag) return
    const lo = Math.min(drag.start, drag.end)
    const hi = Math.max(drag.start, drag.end)
    if (hi - lo >= 1) emitSelect(drag)
    else emitSelect(null)
  }, [drag])

  const hoverPt = hoverIndex != null ? volumeGeom.pts[hoverIndex] : null
  const hoverBaselinePt = hoverIndex != null ? baselineGeom.pts[hoverIndex] : null
  const hoverLeftPct = hoverIndex != null ? indexLeftPct(hoverIndex, count) : null
  const lastPt = volumeGeom.pts[volumeGeom.pts.length - 1]
  const axisMarks24h = period === '24h' ? build24hAxisMarks() : null

  const rangeLeftPct = hasSelection ? indexLeftPct(rangeLo, count) : null
  const rangeRightPct = hasSelection ? indexLeftPct(rangeHi, count) : null
  const rangeInfo = hasSelection ? buildRangeInfo(series, rangeLo, rangeHi) : null
  const startPt = hasSelection ? volumeGeom.pts[rangeLo] : null
  const endPt = hasSelection ? volumeGeom.pts[rangeHi] : null
  const startLeftPct = hasSelection ? indexLeftPct(rangeLo, count) : null
  const endLeftPct = hasSelection ? indexLeftPct(rangeHi, count) : null

  return (
    <motion.div
      key={period}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.35 }}
      className="relative w-full"
    >
      <div
        ref={chartRef}
        className="relative touch-none select-none cursor-crosshair"
        onMouseMove={handleMove}
        onMouseLeave={handleLeave}
        onPointerDown={handlePointerDown}
      >
        <div className="relative h-[220px] w-full sm:h-[260px] lg:h-[280px]">
          <svg
            viewBox="0 0 800 220"
            preserveAspectRatio="none"
            className="pointer-events-none absolute inset-0 h-full w-full"
            role="img"
            aria-label="Network volume over time"
          >
            <defs>
              <linearGradient id="volume-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.28" />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.02" />
              </linearGradient>
            </defs>

            <path
              d={baselineGeom.line}
              fill="none"
              stroke="var(--ink-4)"
              strokeWidth="1.5"
              strokeDasharray="4 6"
              vectorEffect="non-scaling-stroke"
            />

            <path d={volumeGeom.area} fill="url(#volume-fill)" />
            <path
              d={volumeGeom.line}
              fill="none"
              stroke="var(--accent)"
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
          </svg>

          {/* HTML overlay — dots, crosshair, brush selection */}
          <div className="pointer-events-none absolute inset-0">
            {hasSelection && rangeLeftPct != null && rangeRightPct != null && (
              <>
                <div
                  className="absolute inset-y-0 left-0 bg-base/55 backdrop-blur-[1px]"
                  style={{ width: `${rangeLeftPct}%` }}
                />
                <div
                  className="absolute inset-y-0 right-0 bg-base/55 backdrop-blur-[1px]"
                  style={{ left: `${rangeRightPct}%` }}
                />
                <div
                  className="absolute inset-y-0 border-x border-accent/35 bg-accent/[0.07]"
                  style={{
                    left: `${rangeLeftPct}%`,
                    width: `${rangeRightPct - rangeLeftPct}%`,
                  }}
                />
                <div
                  className="absolute inset-y-0 w-px bg-accent/50"
                  style={{ left: `${rangeLeftPct}%` }}
                />
                <div
                  className="absolute inset-y-0 w-px bg-accent/50"
                  style={{ left: `${rangeRightPct}%` }}
                />
                {startPt && (
                  <div
                    className="absolute size-[9px] -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-base bg-accent shadow-sm"
                    style={{
                      left: `${startLeftPct}%`,
                      top: `${ptTopPct(startPt.y)}%`,
                    }}
                  />
                )}
                {endPt && (
                  <div
                    className="absolute size-[9px] -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-base bg-accent shadow-sm"
                    style={{
                      left: `${endLeftPct}%`,
                      top: `${ptTopPct(endPt.y)}%`,
                    }}
                  />
                )}
              </>
            )}

            {!hasSelection && hoverLeftPct != null && hoverPt && (
              <>
                <div
                  className="absolute inset-y-0 w-8 -translate-x-1/2"
                  style={{ left: `${hoverLeftPct}%` }}
                >
                  <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-ink-3/20" />
                </div>
                {hoverBaselinePt && (
                  <div
                    className="absolute size-[7px] -translate-x-1/2 -translate-y-1/2 rounded-full border-[1.5px] border-ink-4 bg-base"
                    style={{
                      left: `${hoverLeftPct}%`,
                      top: `${ptTopPct(hoverBaselinePt.y)}%`,
                    }}
                  />
                )}
                <div
                  className="absolute size-[9px] -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-base bg-accent shadow-sm"
                  style={{
                    left: `${hoverLeftPct}%`,
                    top: `${ptTopPct(hoverPt.y)}%`,
                  }}
                />
              </>
            )}

            {!hasSelection && hoverIndex == null && lastPt && (
              <div
                className="absolute size-[7px] -translate-x-1/2 -translate-y-1/2 rounded-full border-[1.5px] border-base bg-accent"
                style={{
                  left: `${indexLeftPct(series.volume.length - 1, series.volume.length)}%`,
                  top: `${ptTopPct(lastPt.y)}%`,
                }}
              />
            )}
          </div>
        </div>

        <AnimatePresence>
          {hasSelection && rangeInfo && (
            <motion.div
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              transition={{ duration: 0.12 }}
              className="pointer-events-none absolute top-2 z-10 -translate-x-1/2 rounded-lg bg-ink px-3 py-2 text-white shadow-lg"
              style={{ left: `${(rangeLeftPct + rangeRightPct) / 2}%` }}
            >
              <div className="whitespace-nowrap font-mono text-[11px] text-white/70">
                {rangeInfo.startLabel} – {rangeInfo.endLabel}
              </div>
              <div className="mt-0.5 whitespace-nowrap font-mono text-[13px] font-semibold tnum">
                {rangeInfo.pctChange >= 0 ? '+' : ''}{rangeInfo.pctChange.toFixed(2)}%
                <span className="ml-2 text-[11px] font-normal text-white/60">return</span>
              </div>
              <div className="mt-0.5 whitespace-nowrap font-mono text-[11px] tnum text-white/70">
                {fmtVolumeM(rangeInfo.startVolume)} → {fmtVolumeM(rangeInfo.endVolume)}
              </div>
            </motion.div>
          )}
          {!hasSelection && hoverIndex != null && (
            <motion.div
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              transition={{ duration: 0.12 }}
              className="pointer-events-none absolute top-2 z-10 -translate-x-1/2 rounded-lg bg-ink px-3 py-2 text-white shadow-lg"
              style={{ left: `${hoverLeftPct}%` }}
            >
              <div className="whitespace-nowrap font-mono text-[11px] text-white/70">
                {series.labels[hoverIndex]}
              </div>
              <div className="mt-0.5 whitespace-nowrap font-mono text-[13px] font-semibold tnum">
                {fmtVolumeM(series.volume[hoverIndex])}
                <span className="ml-2 text-[11px] font-normal text-white/60">settled</span>
              </div>
              <div className="mt-0.5 whitespace-nowrap font-mono text-[11px] tnum text-white/70">
                {fmtVolumeM(series.baseline[hoverIndex])} baseline
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {axisMarks24h ? (
        <div className="relative mt-2 h-2.5">
          {axisMarks24h.map((mark, i) => {
            const markPct = mark.pct * 100
            const inRange = hasSelection && rangeLeftPct != null && rangeRightPct != null
              && markPct >= rangeLeftPct - 0.2 && markPct <= rangeRightPct + 0.2
            const isActive = !hasSelection && hoverLeftPct != null && Math.abs(markPct - hoverLeftPct) < 0.35
            return (
              <span
                key={i}
                className={`absolute top-0 block w-px -translate-x-1/2 ${
                  inRange ? 'h-2.5 bg-accent/70' : isActive ? 'h-2.5 bg-ink-3' : mark.major ? 'h-2 bg-ink-4/70' : 'h-1 bg-line-2'
                }`}
                style={{ left: `${markPct}%` }}
              />
            )
          })}
        </div>
      ) : (
        <div className="mt-1 flex justify-between px-0.5 font-mono text-[10px] text-ink-4 tnum">
          {tickIndices(series.labels.length).map(i => (
            <span key={`${i}-${series.labels[i]}`} className={hoverIndex === i ? 'font-semibold text-ink' : undefined}>
              {series.labels[i]}
            </span>
          ))}
        </div>
      )}
    </motion.div>
  )
}

function StatCard({ label, value, format, delta, tone = 'text-ink' }) {
  const displayed = useCountUp(value, 800)
  const formatted = format === 'currency'
    ? `$${(displayed / 1000000).toFixed(2)}M`
    : displayed.toLocaleString()
  const isUp = delta > 0

  return (
    <div className="min-w-0 flex-1 px-1 py-3">
      <div className="text-[11px] text-ink-3">{label}</div>
      <div className={`mt-0.5 font-mono text-[17px] font-semibold tnum ${tone}`}>{formatted}</div>
      <div className={`mt-0.5 text-[11px] tnum ${isUp ? 'text-accent' : 'text-critical'}`}>
        {isUp ? '+' : ''}{delta}% <span className="text-ink-4">vs prior</span>
      </div>
    </div>
  )
}

function AlertRow({ alert, isOpen, onToggle }) {
  const tone = RISK_VAR[alert.severity]
  return (
    <article className="cursor-pointer py-3.5" onClick={onToggle}>
      <div className="flex items-start gap-2.5">
        <span className="mt-1.5 h-[5px] w-[5px] shrink-0 rounded-full" style={{ background: tone }} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[13px] font-medium text-ink">{alert.type}</span>
            <span className="shrink-0 font-mono text-[10px] text-ink-4">{alert.timestamp}</span>
          </div>
          <p className="mt-0.5 line-clamp-2 text-[12px] leading-snug text-ink-2">{alert.message}</p>
        </div>
      </div>
      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.p
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="ml-3.5 mt-2 overflow-hidden text-[12px] leading-relaxed text-ink-3"
          >
            {alert.aiExplanation}
          </motion.p>
        )}
      </AnimatePresence>
    </article>
  )
}

const FEED_LIMIT = 6

function FeedSectionHeader({ title, count, countLabel, countTone = 'text-ink-4', subtitle, onViewAll }) {
  return (
    <header className="mb-5 border-b border-line-2 pb-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="font-display text-[17px] font-medium leading-tight text-ink">{title}</h2>
          <p className="mt-1 text-[12px] text-ink-3">{subtitle}</p>
        </div>
        <button
          type="button"
          onClick={onViewAll}
          className="shrink-0 pt-0.5 text-[12px] font-medium text-accent hover:opacity-70"
        >
          View all
        </button>
      </div>
      <p className="mt-3 font-mono text-[11px] tnum text-ink-4">
        Showing <span className={`font-semibold ${countTone}`}>{count}</span> {countLabel}
      </p>
    </header>
  )
}

function ActivityRow({ tx }) {
  return (
    <div className="flex items-center gap-3 py-3.5">
      <div className="min-w-0 flex-1">
        <div className="truncate font-mono text-[12px] text-ink-2">
          {tx.from} → {tx.to}
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-[11px] text-ink-4">
          <span className="font-mono tnum">{tx.ts}</span>
          <span>{tx.rail}</span>
        </div>
      </div>
      <div className="shrink-0 text-right">
        <div className="font-mono text-[13px] font-semibold text-ink tnum">
          ${tx.amount.toLocaleString()}
        </div>
        <div className="mt-0.5 flex justify-end gap-1.5">
          <RiskChip level={tx.risk} />
          <StatusChip status={tx.status} />
        </div>
      </div>
    </div>
  )
}

export default function Dashboard({ onNav }) {
  const [period, setPeriod] = useState('24h')
  const [openAlertId, setOpenAlertId] = useState(null)
  const [liveCount, setLiveCount] = useState(88421)
  const [hoverPoint, setHoverPoint] = useState(null)
  const [selectRange, setSelectRange] = useState(null)

  const baseVolumeM = METRICS.volume24h.value / 1_000_000
  const baseDelta = METRICS.volume24h.delta

  const heroLabel = selectRange
    ? `${selectRange.startLabel} – ${selectRange.endLabel}`
    : hoverPoint
      ? hoverPoint.label
      : 'Network volume'

  const targetVolume = selectRange
    ? selectRange.endVolume
    : hoverPoint
      ? hoverPoint.volume
      : baseVolumeM

  const targetPct = selectRange
    ? selectRange.pctChange
    : hoverPoint
      ? ((hoverPoint.volume - hoverPoint.baseline) / hoverPoint.baseline) * 100
      : baseDelta

  const pctContext = selectRange ? 'in period' : hoverPoint ? 'vs baseline' : 'today'

  const targetTxns = selectRange
    ? selectRange.txnsTotal
    : hoverPoint
      ? hoverPoint.txns
      : liveCount

  const txnsContext = selectRange ? 'in this window' : 'processed'
  const pctIsUp = targetPct >= 0

  useEffect(() => {
    const iv = setInterval(() => setLiveCount(n => n + Math.floor(Math.random() * 3 + 1)), 900)
    return () => clearInterval(iv)
  }, [])

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* ── Hero: volume + chart (Wealthsimple-style) ── */}
      <section className="shrink-0 px-8 pb-6 pt-4">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-[13px] text-ink-3">
              {heroLabel}
            </p>
            <div className="mt-1 flex flex-wrap items-baseline gap-3">
              <RollingNumber
                value={targetVolume}
                format={v => `$${v.toFixed(2)}M`}
                className="font-mono text-[36px] font-semibold leading-none tracking-tight text-ink tnum sm:text-[42px]"
              />
              <span className={`inline-flex items-center gap-0 text-[14px] font-medium leading-none tnum ${pctIsUp ? 'text-accent' : 'text-critical'}`}>
                <RollingNumber
                  value={targetPct}
                  format={v => `${v >= 0 ? '+' : ''}${v.toFixed(selectRange ? 2 : 1)}%`}
                />
                <span className="ml-1 font-normal leading-none text-ink-4">
                  {pctContext}
                </span>
              </span>
            </div>
            <p className="mt-2 text-[12px] text-ink-4">
              <RollingNumber
                value={targetTxns}
                format={v => Math.round(v).toLocaleString()}
                className="font-mono text-ink-3 tnum"
              />
              {' '}transactions {txnsContext}
              {selectRange && (
                <span className="ml-2 font-mono tnum text-ink-3">
                  ({selectRange.volumeDelta >= 0 ? '+' : ''}${Math.abs(selectRange.volumeDelta).toFixed(2)}M volume)
                </span>
              )}
            </p>
          </div>

          <div className="flex items-center gap-5">
            {PERIODS.map(p => (
              <button
                key={p.id}
                type="button"
                onClick={() => {
                  setPeriod(p.id)
                  setHoverPoint(null)
                  setSelectRange(null)
                }}
                className={`relative pb-1 text-[13px] font-medium transition-colors
                  ${period === p.id ? 'text-ink' : 'text-ink-3 hover:text-ink-2'}`}
              >
                {p.label}
                {period === p.id && (
                  <motion.span
                    layoutId="volume-period"
                    className="absolute -bottom-0.5 left-0 right-0 h-[2px] rounded-full bg-accent"
                    transition={{ type: 'spring', stiffness: 480, damping: 36 }}
                  />
                )}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-6">
          <VolumeChart
            period={period}
            onHover={setHoverPoint}
            onSelect={setSelectRange}
          />
          <div className="mt-3 flex flex-wrap items-center gap-4 text-[11px] text-ink-4">
            <span className="flex items-center gap-2">
              <span className="h-0.5 w-4 rounded-full bg-accent" />
              Settled volume
            </span>
            <span className="flex items-center gap-2">
              <span className="h-0 w-4 border-t border-dashed border-ink-4" />
              Expected baseline
            </span>
            <button
              type="button"
              onClick={() => onNav('graph')}
              className="ml-auto text-[12px] font-medium text-accent hover:opacity-70"
            >
              Explore network graph →
            </button>
          </div>
        </div>
      </section>

      {/* ── Secondary stats strip ── */}
      <section className="border-y border-line/60 px-8">
        <div className="flex flex-wrap divide-x divide-line/60">
          {STAT_CARDS.map(({ key, label, format, tone }) => (
            <StatCard
              key={key}
              label={label}
              value={METRICS[key].value}
              delta={METRICS[key].delta}
              format={format}
              tone={tone}
            />
          ))}
          <div className="min-w-0 flex-1 px-1 py-3">
            <div className="text-[11px] text-ink-3">Avg settlement</div>
            <div className="mt-0.5 font-mono text-[17px] font-semibold text-ink tnum">4.2m</div>
            <div className="mt-0.5 text-[11px] text-ink-4">hop latency</div>
          </div>
        </div>
      </section>

      {/* ── Peer feeds: equal 50/50 columns on md+ ── */}
      <section className="flex-1 border-t border-line-2 px-8 py-8">
        <div className="grid grid-cols-1 md:grid-cols-2 md:gap-0">
          <div className="min-w-0 md:border-r md:border-line-2 md:pr-8">
            <FeedSectionHeader
              title="Risk alerts"
              count={FEED_LIMIT}
              countLabel={`of ${METRICS.riskAlerts.value} open`}
              countTone="text-critical"
              subtitle="Flagged patterns requiring review"
              onViewAll={() => onNav('alerts')}
            />
            <div className="divide-y divide-line/70">
              {RECENT_ALERTS.slice(0, FEED_LIMIT).map(alert => (
                <AlertRow
                  key={alert.id}
                  alert={alert}
                  isOpen={openAlertId === alert.id}
                  onToggle={() => setOpenAlertId(prev => (prev === alert.id ? null : alert.id))}
                />
              ))}
            </div>
          </div>

          <div className="min-w-0 border-t border-line-2 pt-8 md:border-t-0 md:pl-8 md:pt-0">
            <FeedSectionHeader
              title="Recent activity"
              count={FEED_LIMIT}
              countLabel="live transfers"
              countTone="text-accent"
              subtitle="Latest settled and in-flight transfers"
              onViewAll={() => onNav('transactions')}
            />
            <div className="divide-y divide-line/70">
              {RECENT_TRANSACTIONS.slice(0, FEED_LIMIT).map(tx => (
                <ActivityRow key={tx.id} tx={tx} />
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
