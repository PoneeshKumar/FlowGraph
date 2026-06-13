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
  { label: 'Freeze account', tone: 'text-critical hover:opacity-80' },
  { label: 'Escalate', tone: 'text-high hover:opacity-80' },
  { label: 'False positive', tone: 'text-ink-3 hover:text-ink-2' },
]

function AlertRow({ alert, isOpen, onToggle }) {
  const color = RISK_VAR[alert.severity]

  return (
    <article className="py-5">
      <button type="button" onClick={onToggle} className="w-full cursor-pointer text-left">
        <div className="flex items-start gap-3">
          <span className="mt-2 h-[6px] w-[6px] shrink-0 rounded-full" style={{ background: color }} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
              <span className="text-[14px] font-semibold text-ink">{alert.type}</span>
              <span className="text-[11px] font-bold uppercase tracking-[0.06em]" style={{ color }}>
                {alert.severity}
              </span>
              <span className="font-mono text-[11px] text-ink-4">{alert.timestamp}</span>
            </div>
            <p className="mt-1.5 text-[14px] leading-relaxed text-ink-2">{alert.message}</p>
            <p className="mt-2 font-mono text-[12px] text-ink-3 tnum">
              {alert.account} · ${(alert.amount / 1000).toFixed(0)}K · {alert.confidence}% confidence
            </p>
          </div>
        </div>
      </button>

      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <div className="ml-[18px] mt-4 max-w-3xl">
              <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">
                AI analysis · <span className="font-mono font-normal normal-case">{alert.id}</span>
              </p>
              <p className="mt-2 text-[14px] leading-[1.75] text-ink-2">{alert.aiExplanation}</p>

              <div className="mt-4 flex flex-wrap items-center gap-5">
                {ACTIONS.map(btn => (
                  <button
                    key={btn.label}
                    type="button"
                    onClick={e => e.stopPropagation()}
                    className={`text-[13px] font-medium ${btn.tone}`}
                  >
                    {btn.label}
                  </button>
                ))}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </article>
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
        <button type="button" className="text-[13px] font-medium text-ink-3 transition-colors hover:text-ink">
          Export report
        </button>
      </PageHeader>

      {/* Text-only filters */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-2 px-8 pb-4">
        {FILTERS.map(f => {
          const isActive = filter === f
          const count = f === 'all' ? MORE_ALERTS.length : counts[f] || 0
          const color = f === 'all' ? undefined : RISK_VAR[f]
          return (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`flex items-center gap-1.5 text-[13px] capitalize transition-colors
                ${isActive ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}
              style={isActive && color ? { color } : undefined}
            >
              {f}
              <span className="font-mono text-[11px] tnum opacity-60">{count}</span>
            </button>
          )
        })}
        <span className="ml-auto text-[12px] text-ink-4 tnum">
          {filtered.length} of {MORE_ALERTS.length}
        </span>
      </div>

      {/* Feed — no column headers, no spreadsheet grid */}
      <div className="flex-1 overflow-y-auto px-8">
        <div className="divide-y divide-line/70">
          <AnimatePresence initial={false}>
            {filtered.map((alert, i) => (
              <motion.div
                key={alert.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.35, delay: i * 0.03 }}
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
    </div>
  )
}
