import { useState, useEffect, useMemo, useRef } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { METRICS, RECENT_ALERTS, RECENT_TRANSACTIONS, VOLUME_SERIES } from '../data/mockData'
import { RiskChip, StatusChip, RISK_VAR, useCountUp } from './ui'

const PERIODS = [
  { id: '24h', label: '24H' },
  { id: '7d',  label: '7D'  },
  { id: '30d', label: '30D' },
  { id: '90d', label: '90D' },
]

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

function VolumeChart({ period, onHover }) {
  const series = VOLUME_SERIES[period]
  const chartRef = useRef(null)
  const [hoverIndex, setHoverIndex] = useState(null)

  const volumeGeom = useMemo(
    () => buildPath(series.volume, 800, 220),
    [series.volume],
  )
  const baselineGeom = useMemo(
    () => buildPath(series.baseline, 800, 220),
    [series.baseline],
  )

  const handleMove = (e) => {
    const el = chartRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const ratio = (e.clientX - rect.left) / rect.width
    const idx = Math.round(ratio * (series.volume.length - 1))
    const clamped = Math.max(0, Math.min(series.volume.length - 1, idx))
    setHoverIndex(clamped)
    onHover?.({
      label: series.labels[clamped],
      volume: series.volume[clamped],
      baseline: series.baseline[clamped],
    })
  }

  const handleLeave = () => {
    setHoverIndex(null)
    onHover?.(null)
  }

  const hoverPt = hoverIndex != null ? volumeGeom.pts[hoverIndex] : null
  const hoverBaselinePt = hoverIndex != null ? baselineGeom.pts[hoverIndex] : null
  const tooltipLeft = hoverIndex != null
    ? `${(hoverIndex / (series.volume.length - 1)) * 100}%`
    : undefined

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
        className="relative cursor-crosshair"
        onMouseMove={handleMove}
        onMouseLeave={handleLeave}
      >
        <svg
          viewBox="0 0 800 220"
          preserveAspectRatio="none"
          className="pointer-events-none h-[220px] w-full sm:h-[260px] lg:h-[280px]"
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

          {hoverIndex != null && hoverPt && (
            <>
              <line
                x1={hoverPt.x}
                y1={0}
                x2={hoverPt.x}
                y2={220}
                stroke="var(--ink-3)"
                strokeWidth="1"
                strokeDasharray="3 4"
                vectorEffect="non-scaling-stroke"
                opacity="0.5"
              />
              {hoverBaselinePt && (
                <circle
                  cx={hoverBaselinePt.x}
                  cy={hoverBaselinePt.y}
                  r="4"
                  fill="var(--bg-base)"
                  stroke="var(--ink-4)"
                  strokeWidth="2"
                  vectorEffect="non-scaling-stroke"
                />
              )}
              <circle
                cx={hoverPt.x}
                cy={hoverPt.y}
                r="5"
                fill="var(--bg-base)"
                stroke="var(--accent)"
                strokeWidth="2.5"
                vectorEffect="non-scaling-stroke"
              />
            </>
          )}

          {hoverIndex == null && volumeGeom.pts.length > 0 && (
            <circle
              cx={volumeGeom.pts[volumeGeom.pts.length - 1].x}
              cy={volumeGeom.pts[volumeGeom.pts.length - 1].y}
              r="4"
              fill="var(--accent)"
            />
          )}
        </svg>

        <AnimatePresence>
          {hoverIndex != null && (
            <motion.div
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              transition={{ duration: 0.12 }}
              className="pointer-events-none absolute top-2 z-10 -translate-x-1/2 rounded-lg bg-ink px-3 py-2 text-white shadow-lg"
              style={{ left: tooltipLeft }}
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

      <div className="mt-1 flex justify-between px-0.5 font-mono text-[10px] text-ink-4 tnum">
        {series.labels.map((l, i) => (
          <span key={l} className={hoverIndex === i ? 'font-semibold text-ink' : undefined}>
            {l}
          </span>
        ))}
      </div>
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

function FeedSectionHeader({ title, meta, metaTone = 'text-ink-4', onViewAll }) {
  return (
    <div className="mb-4 flex items-baseline justify-between border-b border-line/60 pb-3">
      <h2 className="font-display text-[17px] font-medium text-ink">
        {title}
        {meta != null && (
          <span className={`ml-2 font-mono text-[13px] font-normal ${metaTone}`}>{meta}</span>
        )}
      </h2>
      <button
        type="button"
        onClick={onViewAll}
        className="shrink-0 text-[12px] font-medium text-accent hover:opacity-70"
      >
        View all
      </button>
    </div>
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

  const volumeDisplay = useCountUp(METRICS.volume24h.value, 1200)
  const delta = METRICS.volume24h.delta
  const isUp = delta > 0

  const headlineVolume = hoverPoint
    ? hoverPoint.volume
    : volumeDisplay / 1000000
  const headlineLabel = hoverPoint?.label

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
              {headlineLabel ? headlineLabel : 'Network volume'}
            </p>
            <div className="mt-1 flex flex-wrap items-baseline gap-3">
              <span className="font-mono text-[36px] font-semibold leading-none tracking-tight text-ink tnum sm:text-[42px]">
                ${headlineVolume.toFixed(2)}M
              </span>
              {!hoverPoint && (
                <span className={`text-[14px] font-medium tnum ${isUp ? 'text-accent' : 'text-critical'}`}>
                  {isUp ? '+' : ''}{delta}%
                  <span className="ml-1 font-normal text-ink-4">today</span>
                </span>
              )}
              {hoverPoint && (
                <span className="text-[14px] font-medium text-ink-3 tnum">
                  {fmtVolumeM(hoverPoint.baseline)} baseline
                </span>
              )}
            </div>
            <p className="mt-2 text-[12px] text-ink-4">
              <span className="font-mono text-ink-3 tnum">{liveCount.toLocaleString()}</span> transactions processed
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
          <VolumeChart period={period} onHover={setHoverPoint} />
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

      {/* ── Peer feeds: equal-weight alerts + activity ── */}
      <section className="flex-1 px-8 py-8">
        <div className="grid grid-cols-1 gap-10 lg:grid-cols-2 lg:gap-0 lg:divide-x lg:divide-line/60">
          <div className="min-w-0 lg:pr-8">
            <FeedSectionHeader
              title="Risk alerts"
              meta={METRICS.riskAlerts.value}
              metaTone="text-critical"
              onViewAll={() => onNav('alerts')}
            />
            <div className="divide-y divide-line/60">
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

          <div className="min-w-0 border-t border-line/60 pt-10 lg:border-t-0 lg:pt-0 lg:pl-8">
            <FeedSectionHeader
              title="Recent activity"
              meta="Live"
              metaTone="text-accent"
              onViewAll={() => onNav('transactions')}
            />
            <div className="divide-y divide-line/60">
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
