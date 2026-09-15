import { useState, useEffect, useMemo, useRef } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { RiskChip, StatusChip, RISK_VAR, useCountUp, useTweenValue, Skeleton, ErrorNote, EmptyNote } from './ui'
import { useDataSource } from '../services/DataSourceProvider'
import { useAsync } from '../hooks/useAsync'
import { fmtAmount, fmtTs, fmtClock, fmtShortDay, fmtDay, fmtIso, flagLabel, shortId, fmtCompact } from '../lib/format'

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
  { id: 'all', label: 'ALL' },
]

function indexLeftPct(i, count) {
  return count <= 1 ? 0 : (i / (count - 1)) * 100
}

const CHART_H = 220

function ptTopPct(y) {
  return (y / CHART_H) * 100
}

function tickIndices(count, maxTicks = 12) {
  if (count <= maxTicks) return [...Array(count).keys()]
  return Array.from({ length: maxTicks }, (_, t) =>
    Math.round(t * (count - 1) / (maxTicks - 1)),
  )
}

/** Backend series → chart series: labels per bucket, volume in millions, per-bucket txns. */
function toChartSeries(s, period) {
  const fmt = period === '24h' ? fmtClock : fmtShortDay
  const txns = s.txns.map((v, i) => (i === 0 ? v : v - s.txns[i - 1]))
  return {
    labels: s.t.map(fmt),
    volume: s.volume.map(v => v / 1e6),
    baseline: s.baseline.map(v => v / 1e6),
    txns,
    end: s.anchor_ts,
  }
}

const STAT_CARDS = [
  { key: 'accounts',        label: 'Accounts',            sub: 'in the graph' },
  { key: 'scored_accounts', label: 'GNN-scored',          sub: 'with a risk score' },
  { key: 'communities',     label: 'Communities',         sub: 'Louvain clusters' },
  { key: 'in_cycle',        label: 'On detected cycles',  sub: 'cycle detector', tone: 'text-critical' },
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
  return `${v.toFixed(2)}M`
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

function VolumeChart({ series, onHover, onSelect }) {
  const chartRef = useRef(null)
  const dragActiveRef = useRef(false)
  const seriesRef = useRef(series)
  const onSelectRef = useRef(onSelect)
  const onHoverRef = useRef(onHover)

  useEffect(() => {
    seriesRef.current = series
    onSelectRef.current = onSelect
    onHoverRef.current = onHover
  }, [series, onSelect, onHover])

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

  const rangeLeftPct = hasSelection ? indexLeftPct(rangeLo, count) : null
  const rangeRightPct = hasSelection ? indexLeftPct(rangeHi, count) : null
  const rangeInfo = hasSelection ? buildRangeInfo(series, rangeLo, rangeHi) : null
  const startPt = hasSelection ? volumeGeom.pts[rangeLo] : null
  const endPt = hasSelection ? volumeGeom.pts[rangeHi] : null
  const startLeftPct = hasSelection ? indexLeftPct(rangeLo, count) : null
  const endLeftPct = hasSelection ? indexLeftPct(rangeHi, count) : null

  return (
    <motion.div
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
            aria-label="Cumulative volume over time"
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
                <span className="ml-2 text-[11px] font-normal text-white/60">growth</span>
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
                <span className="ml-2 text-[11px] font-normal text-white/60">cumulative</span>
              </div>
              <div className="mt-0.5 whitespace-nowrap font-mono text-[11px] tnum text-white/70">
                {fmtVolumeM(series.baseline[hoverIndex])} uniform pace
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className="mt-1 flex justify-between px-0.5 font-mono text-[10px] text-ink-4 tnum">
        {tickIndices(series.labels.length).map(i => (
          <span key={`${i}-${series.labels[i]}`} className={hoverIndex === i ? 'font-semibold text-ink' : undefined}>
            {series.labels[i]}
          </span>
        ))}
      </div>
    </motion.div>
  )
}

function StatCard({ label, value, sub, tone = 'text-ink' }) {
  const displayed = useCountUp(value ?? 0, 800)
  return (
    <div className="min-w-0 flex-1 px-1 py-3">
      <div className="text-[11px] text-ink-3">{label}</div>
      <div className={`mt-0.5 font-mono text-[17px] font-semibold tnum ${tone}`}>{displayed.toLocaleString()}</div>
      <div className="mt-0.5 text-[11px] text-ink-4">{sub}</div>
    </div>
  )
}

function ExpandLink({ onClick, label }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="absolute right-0 top-3.5 flex h-7 w-7 items-center justify-center rounded-md text-ink-4 transition-colors hover:bg-hover hover:text-accent"
      aria-label={label}
    >
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
        <path
          d="M4.5 9.5L9.5 4.5M9.5 4.5H5.5M9.5 4.5V8.5"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  )
}

function AlertRow({ alert, isOpen, onToggle, onExpand }) {
  const tone = RISK_VAR[alert.risk_level]
  const signals = Object.entries(alert.details?.signals || {}).filter(([, v]) => v).map(([k]) => k)
  return (
    <article className="relative pr-9">
      <button type="button" onClick={onToggle} className="w-full cursor-pointer py-3.5 text-left transition-colors hover:bg-hover/60">
        <div className="flex items-start gap-2.5">
          <span className="mt-1.5 h-[5px] w-[5px] shrink-0 rounded-full" style={{ background: tone }} />
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[13px] font-medium text-ink">{flagLabel(alert.flag_type)}</span>
              <span className="shrink-0 font-mono text-[10px] text-ink-4">{fmtIso(alert.last_detected_at)}</span>
            </div>
            <p className="mt-0.5 line-clamp-2 text-[12px] leading-snug text-ink-2">{alert.explanation}</p>
          </div>
        </div>
      </button>
      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }} className="overflow-hidden">
            <div className="ml-3.5 pb-3 pr-1">
              <p className="font-mono text-[11px] text-ink-4 tnum">
                {shortId(alert.primary_account)}{alert.account_ids.length > 1 ? ` +${alert.account_ids.length - 1}` : ''} · score {(alert.risk_score * 100).toFixed(0)}%
                {signals.length > 0 && ` · ${signals.join(', ')}`}
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <ExpandLink onClick={e => { e.stopPropagation(); onExpand() }} label={`Open alert ${alert.id}`} />
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

function ActivityRow({ tx, isOpen, onToggle, onExpand }) {
  return (
    <article className="relative pr-9">
      <button type="button" onClick={onToggle} className="flex w-full cursor-pointer items-center gap-3 py-3.5 text-left transition-colors hover:bg-hover/60">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[12px] text-ink-2">{shortId(tx.sender_id)} → {shortId(tx.receiver_id)}</div>
          <div className="mt-0.5 flex items-center gap-2 text-[11px] text-ink-4">
            <span className="font-mono tnum">{fmtTs(tx.ts)}</span>
            <span>{tx.currency}</span>
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="font-mono text-[13px] font-semibold text-ink tnum">{fmtAmount(tx.amount_cents)}</div>
          <div className="mt-0.5 flex justify-end gap-1.5">
            <RiskChip level={tx.risk_tier} />
            <StatusChip status={tx.flagged ? 'flagged' : 'settled'} />
          </div>
        </div>
      </button>
      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }} className="overflow-hidden">
            <div className="pb-3">
              <p className="font-mono text-[11px] text-ink-3 tnum">{tx.txn_id}</p>
              <p className="mt-1.5 text-[12px] leading-relaxed text-ink-3">
                {tx.event_type || 'Transfer'} of {fmtAmount(tx.amount_cents)} {tx.currency}. Sender {tx.sender_tier}, receiver {tx.receiver_tier}.
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <ExpandLink onClick={e => { e.stopPropagation(); onExpand() }} label={`Open ${tx.txn_id} in Transactions`} />
    </article>
  )
}

export default function Dashboard({ onNav }) {
  const { source } = useDataSource()
  const [period, setPeriod] = useState('7d')
  const [currency, setCurrency] = useState(null)
  const [openAlertId, setOpenAlertId] = useState(null)
  const [openTxId, setOpenTxId] = useState(null)
  const [hoverPoint, setHoverPoint] = useState(null)
  const [selectRange, setSelectRange] = useState(null)

  const overview = useAsync(() => source.stats.overview(), [source.mode])
  const currencies = overview.data?.currencies ?? []
  const activeCurrency = currency ?? currencies[0]?.code ?? null
  const seriesQ = useAsync(
    () => (activeCurrency ? source.stats.series(activeCurrency, period) : Promise.resolve(null)),
    [source.mode, activeCurrency, period],
  )
  const alerts = useAsync(() => source.alerts.list({ limit: FEED_LIMIT, min_level: 'high' }), [source.mode])
  const txns = useAsync(() => source.transactions.list({ limit: FEED_LIMIT }), [source.mode])

  const series = useMemo(() => (seriesQ.data ? toChartSeries(seriesQ.data, period) : null), [seriesQ.data, period])
  const totalM = series ? series.volume[series.volume.length - 1] : 0
  const totalTxns = series ? series.txns.reduce((a, b) => a + b, 0) : 0

  const heroLabel = selectRange ? `${selectRange.startLabel} – ${selectRange.endLabel}`
    : hoverPoint ? hoverPoint.label
    : `${activeCurrency ?? ''} volume · ${PERIODS.find(p => p.id === period)?.label}`
  const targetVolume = selectRange ? selectRange.endVolume : hoverPoint ? hoverPoint.volume : totalM
  const targetTxns = selectRange ? selectRange.txnsTotal : hoverPoint ? hoverPoint.txns : totalTxns
  const pct = selectRange ? selectRange.pctChange
    : hoverPoint && hoverPoint.baseline ? ((hoverPoint.volume - hoverPoint.baseline) / hoverPoint.baseline) * 100 : null
  const ds = overview.data?.dataset

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* ── Hero: volume + chart ── */}
      <section className="shrink-0 px-8 pb-6 pt-4">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-[13px] text-ink-3">{heroLabel}</p>
            <div className="mt-1 flex flex-wrap items-baseline gap-3">
              <RollingNumber value={targetVolume} format={v => `${v.toFixed(2)}M`}
                className="font-mono text-[36px] font-semibold leading-none tracking-tight text-ink tnum sm:text-[42px]" />
              {pct != null && (
                <span className={`inline-flex items-center text-[14px] font-medium leading-none tnum ${pct >= 0 ? 'text-accent' : 'text-critical'}`}>
                  <RollingNumber value={pct} format={v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`} />
                  <span className="ml-1 font-normal leading-none text-ink-4">{selectRange ? 'in range' : 'vs uniform pace'}</span>
                </span>
              )}
            </div>
            <p className="mt-2 text-[12px] text-ink-4">
              <RollingNumber value={targetTxns} format={v => Math.round(v).toLocaleString()} className="font-mono text-ink-3 tnum" />
              {' '}transactions{selectRange ? ' in this window' : hoverPoint ? ' in this bucket' : ''}
            </p>
          </div>

          <div className="flex items-center gap-5">
            <select value={activeCurrency ?? ''} onChange={e => { setCurrency(e.target.value); setHoverPoint(null); setSelectRange(null) }}
              className="border-b border-line bg-transparent pb-1 text-[13px] text-ink-2 outline-none">
              {currencies.map(c => <option key={c.code} value={c.code}>{c.code}</option>)}
            </select>
            {PERIODS.map(p => (
              <button key={p.id} type="button" onClick={() => { setPeriod(p.id); setHoverPoint(null); setSelectRange(null) }}
                className={`relative pb-1 text-[13px] font-medium transition-colors ${period === p.id ? 'text-ink' : 'text-ink-3 hover:text-ink-2'}`}>
                {p.label}
                {period === p.id && (
                  <motion.span layoutId="volume-period" className="absolute -bottom-0.5 left-0 right-0 h-[2px] rounded-full bg-accent"
                    transition={{ type: 'spring', stiffness: 480, damping: 36 }} />
                )}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-6">
          {seriesQ.error ? <ErrorNote error={seriesQ.error} onRetry={seriesQ.reload} />
            : !series ? <Skeleton className="h-[220px] w-full sm:h-[260px] lg:h-[280px]" />
            : <VolumeChart key={`${activeCurrency}-${period}`} series={series} onHover={setHoverPoint} onSelect={setSelectRange} />}
          <div className="mt-3 flex flex-wrap items-center gap-4 text-[11px] text-ink-4">
            <span className="flex items-center gap-2"><span className="h-0.5 w-4 rounded-full bg-accent" />Cumulative volume</span>
            <span className="flex items-center gap-2"><span className="h-0 w-4 border-t border-dashed border-ink-4" />Uniform pace</span>
            {series && <span className="font-mono tnum">ends {fmtDay(series.end)}</span>}
            <button type="button" onClick={() => onNav('graph')} className="ml-auto text-[12px] font-medium text-accent hover:opacity-70">
              Explore network graph →
            </button>
          </div>
        </div>
      </section>

      {/* ── Secondary stats strip ── */}
      <section className="border-y border-line/60 px-8">
        {overview.error ? <div className="py-3"><ErrorNote error={overview.error} onRetry={overview.reload} /></div> : (
          <div className="flex flex-wrap divide-x divide-line/60">
            {STAT_CARDS.map(({ key, label, sub, tone }) => (
              <StatCard key={key} label={label} value={overview.data?.[key]} sub={sub} tone={tone} />
            ))}
            <StatCard label="Open flags" value={overview.data?.open_flags?.total} sub="across all detectors" tone="text-high" />
            <div className="min-w-0 flex-1 px-1 py-3">
              <div className="text-[11px] text-ink-3">Dataset</div>
              <div className="mt-0.5 font-mono text-[13px] font-semibold text-ink tnum">{ds ? `${fmtDay(ds.start_ts)} – ${fmtDay(ds.end_ts)}` : '—'}</div>
              <div className="mt-0.5 text-[11px] text-ink-4">{ds?.name ?? ''} · {fmtCompact(overview.data?.transactions ?? 0)} transactions</div>
            </div>
          </div>
        )}
      </section>

      {/* ── Peer feeds ── */}
      <section className="flex-1 border-t border-line-2 px-8 py-8">
        <div className="grid grid-cols-1 md:grid-cols-2 md:gap-0">
          <div className="min-w-0 md:border-r md:border-line-2 md:pr-8">
            <FeedSectionHeader title="Risk alerts" count={alerts.data?.items.length ?? 0}
              countLabel={`of ${(alerts.data?.total ?? 0).toLocaleString()} high or critical`} countTone="text-critical"
              subtitle="Highest-scoring open flags" onViewAll={() => onNav('alerts')} />
            {alerts.error ? <ErrorNote error={alerts.error} onRetry={alerts.reload} />
              : alerts.loading ? <Skeleton className="h-40 w-full" />
              : alerts.data.items.length === 0 ? <EmptyNote>No open alerts.</EmptyNote> : (
              <div className="divide-y divide-line/70">
                {alerts.data.items.map(alert => (
                  <AlertRow key={alert.id} alert={alert} isOpen={openAlertId === alert.id}
                    onToggle={() => setOpenAlertId(prev => (prev === alert.id ? null : alert.id))}
                    onExpand={() => onNav('alerts', { id: alert.id })} />
                ))}
              </div>
            )}
          </div>

          <div className="min-w-0 border-t border-line-2 pt-8 md:border-t-0 md:pl-8 md:pt-0">
            <FeedSectionHeader title="Latest transfers" count={txns.data?.items.length ?? 0} countLabel="most recent" countTone="text-accent"
              subtitle="Newest settled transfers in the dataset" onViewAll={() => onNav('transactions')} />
            {txns.error ? <ErrorNote error={txns.error} onRetry={txns.reload} />
              : txns.loading ? <Skeleton className="h-40 w-full" />
              : txns.data.items.length === 0 ? <EmptyNote>No transactions.</EmptyNote> : (
              <div className="divide-y divide-line/70">
                {txns.data.items.map(tx => (
                  <ActivityRow key={tx.txn_id} tx={tx} isOpen={openTxId === tx.txn_id}
                    onToggle={() => setOpenTxId(prev => (prev === tx.txn_id ? null : tx.txn_id))}
                    onExpand={() => onNav('transactions', { account: tx.sender_id })} />
                ))}
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  )
}
