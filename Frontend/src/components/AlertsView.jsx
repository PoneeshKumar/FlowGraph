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

function AlertRow({ alert, isOpen, onToggle }) {
  const color = RISK_VAR[alert.severity]

  return (
    <div className="border-b border-line">
      {/* Summary row — dense, scannable */}
      <div
        onClick={onToggle}
        className={`grid cursor-pointer grid-cols-[3px_88px_180px_1fr_96px_104px_72px] items-center gap-4 px-7 py-3 transition-colors duration-150
          ${isOpen ? 'bg-hover' : 'hover:bg-hover'}`}
      >
        <span className="h-8 w-[3px] rounded-full" style={{ background: color, opacity: isOpen ? 1 : 0.65 }} />

        <span className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color }}>
          {alert.severity}
        </span>

        <span className="truncate text-[13px] font-medium text-ink">{alert.type}</span>

        <span className="truncate text-[12.5px] text-ink-2">{alert.message}</span>

        <span className="font-mono text-xs text-ink-2">{alert.account}</span>

        <span className="text-right font-mono text-xs font-semibold text-ink tnum">
          ${(alert.amount / 1000).toFixed(0)}K
        </span>

        <span className="text-right text-[11px] text-ink-4">{alert.timestamp}</span>
      </div>

      {/* Inline detail — pours open under the row */}
      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            initial={{ height: 0 }}
            animate={{ height: 'auto' }}
            exit={{ height: 0 }}
            transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <motion.div
              initial={{ opacity: 0, y: -14, filter: 'blur(5px)' }}
              animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
              exit={{ opacity: 0, transition: { duration: 0.16 } }}
              transition={{ duration: 0.45, delay: 0.07, ease: [0.22, 1, 0.36, 1] }}
              className="grid grid-cols-[1fr_240px] gap-8 bg-hover px-7 pb-5 pt-1 pl-[27px]">
              {/* AI analysis */}
              <div className="border-l-2 pl-4" style={{ borderColor: color }}>
                <div className="mb-1.5 flex items-center gap-2.5">
                  <span className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-3">AI Analysis</span>
                  <span className="font-mono text-[10px] text-ink-4">{alert.id}</span>
                </div>
                <p className="m-0 max-w-[720px] text-[13px] leading-[1.75] text-ink-2">
                  {alert.aiExplanation}
                </p>

                {/* Confidence inline */}
                <div className="mt-3 flex items-center gap-3">
                  <span className="text-[11px] text-ink-3">Confidence</span>
                  <div className="h-1 w-[180px] overflow-hidden rounded-full bg-line">
                    <motion.div
                      className="h-full rounded-full"
                      style={{ background: color }}
                      initial={{ width: 0 }}
                      animate={{ width: `${alert.confidence}%` }}
                      transition={{ duration: 0.6, ease: [0.32, 0.72, 0, 1], delay: 0.1 }}
                    />
                  </div>
                  <span className="font-mono text-[11px] font-bold tnum" style={{ color }}>
                    {alert.confidence}%
                  </span>
                </div>
              </div>

              {/* Actions */}
              <div className="flex flex-col justify-center gap-1.5">
                {ACTIONS.map(btn => (
                  <button
                    key={btn.label}
                    onClick={e => e.stopPropagation()}
                    className={`rounded-lg border px-3.5 py-2 text-left text-xs font-medium tracking-tight transition-colors duration-200 ${btn.tone}`}
                  >
                    {btn.label}
                  </button>
                ))}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export default function AlertsView() {
  const [openId, setOpenId] = useState(null)
  const [filter, setFilter] = useState('all')

  const filtered = MORE_ALERTS.filter(a => filter === 'all' || a.severity === filter)
  const counts = MORE_ALERTS.reduce((acc, a) => { acc[a.severity] = (acc[a.severity] || 0) + 1; return acc }, {})

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader title="Risk Alerts" subtitle="AI-generated explanations for every flag">
        <button className="flex items-center gap-2 rounded-lg border border-line px-3.5 py-2 text-xs font-medium text-ink-2 transition-colors duration-200 hover:bg-hover hover:text-ink">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path d="M2 9.5h8M2 6h8M2 2.5h8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
          Export Report
        </button>
      </PageHeader>

      {/* Filter row — flat, sits on the same surface */}
      <div className="flex items-center gap-1 border-b border-line px-7 py-2.5">
        {FILTERS.map(f => {
          const isActive = filter === f
          const count = f === 'all' ? MORE_ALERTS.length : counts[f] || 0
          const color = f === 'all' ? 'var(--ink)' : RISK_VAR[f]
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`relative flex items-center gap-1.5 rounded-full px-3 py-1 text-xs capitalize transition-colors duration-200
                ${isActive ? 'font-semibold' : 'text-ink-3 hover:text-ink-2'}`}
              style={isActive ? { color } : undefined}
            >
              {isActive && (
                <motion.span
                  layoutId="alert-filter-pill"
                  className="absolute inset-0 rounded-full bg-hover ring-1 ring-line-2"
                  transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                />
              )}
              <span className="relative z-[1]">{f}</span>
              <span className="relative z-[1] font-mono text-[10px] tnum opacity-70">{count}</span>
            </button>
          )
        })}

        <span className="ml-auto text-[11px] text-ink-4 tnum">
          {filtered.length} of {MORE_ALERTS.length} alerts
        </span>
      </div>

      {/* Column labels */}
      <div className="grid grid-cols-[3px_88px_180px_1fr_96px_104px_72px] gap-4 border-b border-line px-7 py-2">
        <span />
        {['Severity', 'Type', 'Description', 'Account', 'Amount', 'Age'].map((h, i) => (
          <span key={h} className={`text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4 ${i >= 4 ? 'text-right' : ''}`}>
            {h}
          </span>
        ))}
      </div>

      {/* Ledger */}
      <div className="flex-1 overflow-y-auto">
        <AnimatePresence initial={false}>
          {filtered.map((alert, i) => (
            <motion.div
              key={alert.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, transition: { duration: 0.15 } }}
              transition={{ duration: 0.4, delay: i * 0.035, ease: [0.22, 1, 0.36, 1] }}
            >
              <AlertRow
                alert={alert}
                isOpen={openId === alert.id}
                onToggle={() => setOpenId(prev => (prev === alert.id ? null : alert.id))}
              />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
