import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { METRICS, RECENT_ALERTS, RECENT_TRANSACTIONS } from '../data/mockData'
import { RiskChip, StatusChip, RISK_VAR, useCountUp, PageHeader, TH } from './ui'

const rise = (i = 0) => ({
  initial: { opacity: 0, y: 14 },
  animate: { opacity: 1, y: 0 },
  transition: { delay: 0.06 * i, duration: 0.45, ease: [0.32, 0.72, 0, 1] },
})

function MetricCard({ label, value, format, delta, tone = 'text-ink', index }) {
  const displayed = useCountUp(value, 1000, index * 90)
  const formatted = format === 'currency'
    ? `$${(displayed / 1000000).toFixed(2)}M`
    : displayed.toLocaleString()

  const isUp = delta > 0
  const isBad = (label === 'Cycles Detected' || label === 'Risk Alerts') ? isUp : !isUp

  return (
    <motion.div
      {...rise(index)}
      className="glass group rounded-xl px-5 py-4 transition-transform duration-300 ease-[var(--ease-fluid)] hover:-translate-y-0.5"
    >
      <div className="mb-2.5 text-[10.5px] font-bold uppercase tracking-[0.11em] text-ink-3">
        {label}
      </div>
      <div className={`font-mono text-[26px] font-semibold leading-none tracking-tight tnum ${tone}`}>
        {formatted}
      </div>
      <div className={`mt-2.5 flex items-center gap-1 text-[11px] ${isBad ? 'text-critical' : 'text-accent'}`}>
        <span>{isUp ? '↑' : '↓'}</span>
        <span className="font-semibold tnum">{Math.abs(delta)}%</span>
        <span className="text-ink-4">vs yesterday</span>
      </div>
    </motion.div>
  )
}

function AlertRow({ alert, isSelected, onSelect }) {
  const tone = RISK_VAR[alert.severity]
  return (
    <div
      onClick={() => onSelect(alert)}
      className={`mb-1.5 cursor-pointer rounded-lg border px-3.5 py-2.5 transition-all duration-200
        ${isSelected ? 'border-line-2 bg-hover' : 'border-transparent hover:bg-hover'}`}
    >
      <div className="flex items-start gap-2.5">
        <span className="mt-1.5 h-[5px] w-[5px] shrink-0 rounded-full" style={{ background: tone }} />
        <div className="min-w-0 flex-1">
          <div className="mb-0.5 flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold" style={{ color: tone }}>{alert.type}</span>
            <span className="whitespace-nowrap font-mono text-[10px] text-ink-4">{alert.timestamp}</span>
          </div>
          <div className="text-xs leading-relaxed text-ink-2">{alert.message}</div>
          <div className="mt-1.5 flex items-center gap-2">
            <span className="rounded border border-line bg-hover px-1.5 py-px font-mono text-[10px] text-ink-3 tnum">
              ${(alert.amount / 1000).toFixed(0)}K
            </span>
            <span className="text-[10px] text-ink-4 tnum">{alert.confidence}% confidence</span>
          </div>
        </div>
      </div>

      <AnimatePresence initial={false}>
        {isSelected && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.32, 0.72, 0, 1] }}
            className="overflow-hidden"
          >
            <div className="ml-4 mt-2.5 rounded-lg border border-line bg-hover px-3 py-2.5"
              style={{ borderLeft: `2px solid ${tone}` }}>
              <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.1em] text-ink-3">
                AI Analysis
              </div>
              <div className="text-xs leading-[1.7] text-ink-2">{alert.aiExplanation}</div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

const TX_FILTERS = ['All', 'Flagged', 'Cleared']

export default function Dashboard({ onNav }) {
  const [selectedAlert, setSelectedAlert] = useState(null)
  const [txFilter, setTxFilter] = useState('All')
  const [liveCount, setLiveCount] = useState(88421)

  useEffect(() => {
    const iv = setInterval(() => setLiveCount(n => n + Math.floor(Math.random() * 3 + 1)), 900)
    return () => clearInterval(iv)
  }, [])

  const transactions = RECENT_TRANSACTIONS.filter(tx => {
    if (txFilter === 'Flagged') return tx.status === 'flagged' || tx.status === 'frozen'
    if (txFilter === 'Cleared') return tx.status === 'cleared'
    return true
  })

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title="Network Overview"
        subtitle={
          <>
            Real-time graph intelligence ·{' '}
            <span className="font-mono text-ink-2 tnum">{liveCount.toLocaleString()}</span>{' '}
            transactions processed
          </>
        }
      >
        <button
          onClick={() => onNav('graph')}
          className="group flex items-center gap-2 rounded-lg bg-accent/15 px-3.5 py-2 text-xs font-semibold text-accent ring-1 ring-accent/30 transition-all duration-200 hover:bg-accent/25"
        >
          Open Graph
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" className="transition-transform duration-200 group-hover:translate-x-0.5">
            <path d="M2 5h6M6 3l2 2-2 2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </button>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-4">
        {/* Metric cards */}
        <div className="mb-4 grid grid-cols-4 gap-3.5">
          <MetricCard index={0} label="Volume 24h"      value={METRICS.volume24h.value}      format="currency" delta={METRICS.volume24h.delta}      tone="text-accent" />
          <MetricCard index={1} label="Active Accounts" value={METRICS.activeAccounts.value} format="count"    delta={METRICS.activeAccounts.delta} />
          <MetricCard index={2} label="Cycles Detected" value={METRICS.cyclesDetected.value} format="count"    delta={METRICS.cyclesDetected.delta} tone="text-critical" />
          <MetricCard index={3} label="Risk Alerts"     value={METRICS.riskAlerts.value}     format="count"    delta={METRICS.riskAlerts.delta}     tone="text-high" />
        </div>

        <div className="grid grid-cols-[1fr_1.6fr] items-start gap-4">
          {/* Alerts panel */}
          <motion.section {...rise(3)} className="glass flex flex-col overflow-hidden rounded-xl">
            <div className="flex items-center justify-between border-b border-line px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="font-display text-[14px] font-medium text-ink">Risk Alerts</span>
                <span className="rounded-full border border-critical/30 bg-critical/10 px-1.5 py-px font-mono text-[10px] font-bold text-critical tnum">17</span>
              </div>
              <button
                onClick={() => onNav('alerts')}
                className="text-[11px] font-medium text-accent transition-opacity hover:opacity-75"
              >
                View all →
              </button>
            </div>
            <div className="max-h-[440px] flex-1 overflow-y-auto p-2.5">
              {RECENT_ALERTS.map(alert => (
                <AlertRow
                  key={alert.id}
                  alert={alert}
                  isSelected={selectedAlert?.id === alert.id}
                  onSelect={a => setSelectedAlert(prev => (prev?.id === a.id ? null : a))}
                />
              ))}
            </div>
          </motion.section>

          {/* Transactions table */}
          <motion.section {...rise(4)} className="glass flex flex-col overflow-hidden rounded-xl">
            <div className="flex items-center justify-between border-b border-line px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="font-display text-[14px] font-medium text-ink">Recent Transactions</span>
                <span className="h-1.5 w-1.5 rounded-full bg-accent [animation:pulseSoft_2s_ease-in-out_infinite]" />
              </div>
              <div className="flex gap-1">
                {TX_FILTERS.map(f => (
                  <button
                    key={f}
                    onClick={() => setTxFilter(f)}
                    className={`relative rounded-full px-2.5 py-1 text-[11px] transition-colors duration-200
                      ${txFilter === f ? 'font-semibold text-accent' : 'text-ink-3 hover:text-ink-2'}`}
                  >
                    {txFilter === f && (
                      <motion.span
                        layoutId="dash-tx-filter"
                        className="absolute inset-0 rounded-full bg-accent/10 ring-1 ring-accent/25"
                        transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                      />
                    )}
                    <span className="relative z-[1]">{f}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="border-b border-line">
                    {['Transaction', 'Route', 'Amount', 'Rail', 'Risk', 'Status', 'Time'].map(h => <TH key={h}>{h}</TH>)}
                  </tr>
                </thead>
                <tbody>
                  <AnimatePresence initial={false} mode="popLayout">
                    {transactions.map((tx, i) => (
                      <motion.tr
                        key={tx.id}
                        layout
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.25, delay: i * 0.02 }}
                        className="border-b border-line transition-colors duration-150 last:border-b-0 hover:bg-hover"
                      >
                        <td className="px-4 py-2.5 font-mono text-[10.5px] text-ink-3">{tx.id}</td>
                        <td className="px-4 py-2.5 font-mono text-[11px] text-ink-2">
                          {tx.from}
                          <span className="mx-1.5 text-ink-4">→</span>
                          {tx.to}
                        </td>
                        <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs font-semibold text-ink tnum">
                          ${tx.amount.toLocaleString()}
                        </td>
                        <td className="px-4 py-2.5">
                          <span className="rounded border border-line bg-hover px-1.5 py-px font-mono text-[10px] text-ink-2">
                            {tx.rail}
                          </span>
                        </td>
                        <td className="px-4 py-2.5"><RiskChip level={tx.risk} /></td>
                        <td className="px-4 py-2.5"><StatusChip status={tx.status} /></td>
                        <td className="px-4 py-2.5 font-mono text-[10.5px] text-ink-3 tnum">{tx.ts}</td>
                      </motion.tr>
                    ))}
                  </AnimatePresence>
                </tbody>
              </table>
            </div>
          </motion.section>
        </div>
      </div>
    </div>
  )
}
