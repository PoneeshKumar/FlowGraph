import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { METRICS, RECENT_ALERTS, RECENT_TRANSACTIONS } from '../data/mockData'
import { RiskChip, StatusChip, RISK_VAR, useCountUp, PageHeader } from './ui'

function Metric({ label, value, format, delta, tone = 'text-ink', index }) {
  const displayed = useCountUp(value, 1000, index * 80)
  const formatted = format === 'currency'
    ? `$${(displayed / 1000000).toFixed(2)}M`
    : displayed.toLocaleString()

  const isUp = delta > 0
  const isBad = (label === 'Cycles Detected' || label === 'Risk Alerts') ? isUp : !isUp

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.05 * index, duration: 0.4, ease: [0.32, 0.72, 0, 1] }}
      className="min-w-[140px]"
    >
      <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.12em] text-ink-4">{label}</div>
      <div className={`font-mono text-[22px] font-semibold leading-none tracking-tight tnum ${tone}`}>
        {formatted}
      </div>
      <div className={`mt-1.5 text-[11px] ${isBad ? 'text-critical' : 'text-accent'}`}>
        {isUp ? '↑' : '↓'} <span className="font-semibold tnum">{Math.abs(delta)}%</span>
        <span className="text-ink-4"> vs yesterday</span>
      </div>
    </motion.div>
  )
}

function AlertRow({ alert, isOpen, onToggle }) {
  const tone = RISK_VAR[alert.severity]
  return (
    <article className="cursor-pointer py-4" onClick={onToggle}>
      <div className="flex items-start gap-3">
        <span className="mt-2 h-[6px] w-[6px] shrink-0 rounded-full" style={{ background: tone }} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <span className="text-[13px] font-semibold text-ink">{alert.type}</span>
            <span className="text-[11px] font-bold uppercase tracking-[0.06em]" style={{ color: tone }}>
              {alert.severity}
            </span>
            <span className="font-mono text-[11px] text-ink-4">{alert.timestamp}</span>
          </div>
          <p className="mt-1 text-[13px] leading-relaxed text-ink-2">{alert.message}</p>
          <p className="mt-1 font-mono text-[12px] text-ink-3 tnum">
            {alert.account} · ${(alert.amount / 1000).toFixed(0)}K · {alert.confidence}% confidence
          </p>
        </div>
      </div>

      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <p className="ml-[18px] mt-3 max-w-2xl text-[13px] leading-[1.7] text-ink-2">
              {alert.aiExplanation}
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </article>
  )
}

function TxRow({ tx, index }) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3, delay: index * 0.03 }}
      className="flex flex-wrap items-center gap-x-4 gap-y-1 py-3.5"
    >
      <span className="font-mono text-[11px] text-ink-4">{tx.id}</span>
      <span className="min-w-0 flex-1 font-mono text-[12px] text-ink-2">
        {tx.from}<span className="mx-1.5 text-ink-4">→</span>{tx.to}
      </span>
      <span className="font-mono text-[13px] font-semibold text-ink tnum">${tx.amount.toLocaleString()}</span>
      <span className="font-mono text-[11px] text-ink-3">{tx.rail}</span>
      <RiskChip level={tx.risk} />
      <StatusChip status={tx.status} />
      <span className="font-mono text-[11px] text-ink-4 tnum">{tx.ts}</span>
    </motion.div>
  )
}

const TX_FILTERS = ['All', 'Flagged', 'Cleared']

export default function Dashboard({ onNav }) {
  const [openAlertId, setOpenAlertId] = useState(null)
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
    <div className="flex h-full flex-col overflow-y-auto">
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
          className="text-[13px] font-semibold text-accent transition-opacity hover:opacity-70"
        >
          Open graph →
        </button>
      </PageHeader>

      <div className="px-8">
        {/* Metrics — horizontal strip, no grid boxes */}
        <div className="flex flex-wrap gap-x-12 gap-y-6 pb-10">
          <Metric index={0} label="Volume 24h"      value={METRICS.volume24h.value}      format="currency" delta={METRICS.volume24h.delta}      tone="text-accent" />
          <Metric index={1} label="Active Accounts" value={METRICS.activeAccounts.value} format="count"    delta={METRICS.activeAccounts.delta} />
          <Metric index={2} label="Cycles Detected" value={METRICS.cyclesDetected.value} format="count"    delta={METRICS.cyclesDetected.delta} tone="text-critical" />
          <Metric index={3} label="Risk Alerts"     value={METRICS.riskAlerts.value}     format="count"    delta={METRICS.riskAlerts.delta}     tone="text-high" />
        </div>

        {/* Stacked sections — no column split, no tables */}
        <section className="pb-12">
          <div className="mb-4 flex items-baseline justify-between">
            <h2 className="font-display text-[18px] font-medium text-ink">
              Risk alerts <span className="font-mono text-[14px] text-critical">17</span>
            </h2>
            <button onClick={() => onNav('alerts')} className="text-[12px] font-medium text-accent hover:opacity-70">
              View all
            </button>
          </div>
          <div className="divide-y divide-line/70">
            {RECENT_ALERTS.map(alert => (
              <AlertRow
                key={alert.id}
                alert={alert}
                isOpen={openAlertId === alert.id}
                onToggle={() => setOpenAlertId(prev => (prev === alert.id ? null : alert.id))}
              />
            ))}
          </div>
        </section>

        <section className="pb-10">
          <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="flex items-center gap-2 font-display text-[18px] font-medium text-ink">
              Recent transactions
              <span className="h-1.5 w-1.5 rounded-full bg-accent [animation:pulseSoft_2s_ease-in-out_infinite]" />
            </h2>
            <div className="flex gap-4">
              {TX_FILTERS.map(f => (
                <button
                  key={f}
                  onClick={() => setTxFilter(f)}
                  className={`text-[12px] transition-colors ${txFilter === f ? 'font-semibold text-accent' : 'text-ink-3 hover:text-ink-2'}`}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>
          <div className="divide-y divide-line/70">
            <AnimatePresence initial={false} mode="popLayout">
              {transactions.map((tx, i) => (
                <TxRow key={tx.id} tx={tx} index={i} />
              ))}
            </AnimatePresence>
          </div>
        </section>
      </div>
    </div>
  )
}
