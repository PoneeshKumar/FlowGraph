import { useState } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { RECENT_ALERTS } from '../data/mockData'
import { RISK_VAR, PageHeader } from './ui'

const MORE_ALERTS = [
  ...RECENT_ALERTS,
  {
    id: 'ALT-006', severity: 'medium', type: 'Velocity Anomaly',
    message: 'BNK-1102 sent 12 transactions in 30 min — 6× baseline',
    account: 'BNK-1102', amount: 380000, timestamp: '2 hr ago', confidence: 68,
    aiExplanation: 'Transaction velocity for BNK-1102 is 6× its 30-day average. No prior suspicious activity on record, but pattern warrants monitoring.',
  },
  {
    id: 'ALT-007', severity: 'low', type: 'New Account Flag',
    message: 'EXC-0044 — created 6 days ago, already $1.55M in volume',
    account: 'EXC-0044', amount: 1550000, timestamp: '3 hr ago', confidence: 55,
    aiExplanation: 'EXC-0044 was created 6 days ago and has already processed $1.55M in transactions. Rapid ramp-up is a soft signal for shell account behavior.',
  },
]

const FILTERS = ['all', 'critical', 'high', 'medium', 'low']

const ACTIONS = [
  { label: 'Freeze Account',         tone: 'border-critical/30 bg-critical/10 text-critical hover:bg-critical/20' },
  { label: 'Escalate to Compliance', tone: 'border-high/30 bg-high/10 text-high hover:bg-high/20' },
  { label: 'Mark False Positive',    tone: 'border-line bg-hover text-ink-2 hover:bg-line' },
]

export default function AlertsView() {
  const [selected, setSelected] = useState(null)
  const [filter, setFilter] = useState('all')

  const filtered = MORE_ALERTS.filter(a => filter === 'all' || a.severity === filter)
  const counts = MORE_ALERTS.reduce((acc, a) => { acc[a.severity] = (acc[a.severity] || 0) + 1; return acc }, {})

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader title="Risk Alerts" subtitle="AI-generated explanations for every flag">
        <button className="glass-soft flex items-center gap-2 rounded-lg px-3.5 py-2 text-xs font-medium text-ink-2 transition-colors duration-200 hover:text-ink">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path d="M2 9.5h8M2 6h8M2 2.5h8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
          Export Report
        </button>
      </PageHeader>

      {/* Filter tabs */}
      <div className="mx-4 mt-3 flex gap-1">
        {FILTERS.map(f => {
          const isActive = filter === f
          const count = f === 'all' ? MORE_ALERTS.length : counts[f] || 0
          const color = f === 'all' ? 'var(--ink)' : RISK_VAR[f]
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`relative flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs capitalize transition-colors duration-200
                ${isActive ? 'font-semibold' : 'text-ink-3 hover:text-ink-2'}`}
              style={isActive ? { color } : undefined}
            >
              {isActive && (
                <motion.span
                  layoutId="alert-filter-pill"
                  className="glass absolute inset-0 rounded-full"
                  transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                />
              )}
              <span className="relative z-[1]">{f}</span>
              <span className="relative z-[1] font-mono text-[10px] tnum opacity-70">{count}</span>
            </button>
          )
        })}
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <div className={`grid items-start gap-4 ${selected ? 'grid-cols-[1fr_352px]' : 'grid-cols-1'}`}>
          {/* List */}
          <div>
            <AnimatePresence initial={false} mode="popLayout">
              {filtered.map((alert, i) => {
                const color = RISK_VAR[alert.severity]
                const isSelected = selected?.id === alert.id
                return (
                  <motion.div
                    key={alert.id}
                    layout
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.97 }}
                    transition={{ duration: 0.32, delay: i * 0.035, ease: [0.32, 0.72, 0, 1] }}
                    onClick={() => setSelected(prev => (prev?.id === alert.id ? null : alert))}
                    className={`glass mb-2.5 cursor-pointer rounded-xl px-4.5 py-4 transition-all duration-200 hover:-translate-y-px
                      ${isSelected ? 'ring-1' : ''}`}
                    style={{
                      borderLeft: `2px solid ${isSelected ? color : 'transparent'}`,
                      ...(isSelected ? { ringColor: `color-mix(in oklab, ${color} 40%, transparent)` } : {}),
                    }}
                  >
                    <div className="flex items-start gap-3.5">
                      <div
                        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] border"
                        style={{
                          borderColor: `color-mix(in oklab, ${color} 30%, transparent)`,
                          background: `color-mix(in oklab, ${color} 10%, transparent)`,
                        }}
                      >
                        <span className="h-2 w-2 rounded-full" style={{ background: color }} />
                      </div>

                      <div className="min-w-0 flex-1">
                        <div className="mb-1 flex items-center gap-2">
                          <span className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color }}>
                            {alert.severity}
                          </span>
                          <span className="font-display text-[13.5px] font-medium text-ink">{alert.type}</span>
                          <span className="ml-auto whitespace-nowrap font-mono text-[10px] text-ink-4">{alert.id}</span>
                        </div>
                        <div className="mb-2.5 text-[13px] leading-normal text-ink-2">{alert.message}</div>
                        <div className="flex items-center gap-2.5">
                          <span className="rounded border border-line bg-hover px-1.5 py-px font-mono text-[11px] text-ink-2">
                            {alert.account}
                          </span>
                          <span className="font-mono text-[11px] font-bold text-ink tnum">
                            ${(alert.amount / 1000).toFixed(0)}K
                          </span>
                          <span className="text-[11px] text-ink-4">·</span>
                          <span className="text-[11px] tnum" style={{ color }}>{alert.confidence}% confidence</span>
                          <span className="ml-auto text-[11px] text-ink-4">{alert.timestamp}</span>
                        </div>
                      </div>
                    </div>
                  </motion.div>
                )
              })}
            </AnimatePresence>
          </div>

          {/* Detail panel */}
          <AnimatePresence>
            {selected && (() => {
              const color = RISK_VAR[selected.severity]
              return (
                <motion.aside
                  key={selected.id}
                  initial={{ opacity: 0, x: 28, filter: 'blur(4px)' }}
                  animate={{ opacity: 1, x: 0, filter: 'blur(0px)' }}
                  exit={{ opacity: 0, x: 28, filter: 'blur(4px)' }}
                  transition={{ duration: 0.3, ease: [0.32, 0.72, 0, 1] }}
                  className="glass-strong sticky top-0 rounded-xl p-5"
                  style={{ borderTop: `2px solid ${color}` }}
                >
                  <div className="mb-4 flex items-start justify-between">
                    <div>
                      <div className="mb-1 font-mono text-[10px] text-ink-4">{selected.id}</div>
                      <div className="font-display text-[17px] font-medium text-ink">{selected.type}</div>
                    </div>
                    <button
                      onClick={() => setSelected(null)}
                      className="flex h-[22px] w-[22px] items-center justify-center rounded-full border border-line bg-hover text-sm text-ink-3 transition-colors hover:text-ink"
                    >
                      ×
                    </button>
                  </div>

                  {/* Confidence */}
                  <div className="mb-4">
                    <div className="mb-1.5 flex justify-between">
                      <span className="text-[11px] text-ink-3">AI Confidence</span>
                      <span className="font-mono text-xs font-bold tnum" style={{ color }}>{selected.confidence}%</span>
                    </div>
                    <div className="h-1 overflow-hidden rounded-full bg-line">
                      <motion.div
                        className="h-full rounded-full"
                        style={{ background: color }}
                        initial={{ width: 0 }}
                        animate={{ width: `${selected.confidence}%` }}
                        transition={{ duration: 0.7, ease: [0.32, 0.72, 0, 1], delay: 0.15 }}
                      />
                    </div>
                  </div>

                  {/* AI explanation */}
                  <div className="mb-4 rounded-lg border border-line bg-hover px-3.5 py-3" style={{ borderLeft: `2px solid ${color}` }}>
                    <div className="mb-1.5 text-[10px] font-bold uppercase tracking-[0.1em] text-ink-3">AI Analysis</div>
                    <div className="text-[12.5px] leading-[1.7] text-ink-2">{selected.aiExplanation}</div>
                  </div>

                  {/* Actions */}
                  <div className="flex flex-col gap-1.5">
                    {ACTIONS.map(btn => (
                      <button
                        key={btn.label}
                        className={`rounded-lg border px-3.5 py-2 text-left text-xs font-medium tracking-tight transition-colors duration-200 ${btn.tone}`}
                      >
                        {btn.label}
                      </button>
                    ))}
                  </div>
                </motion.aside>
              )
            })()}
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}
