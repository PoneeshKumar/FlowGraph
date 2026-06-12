import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import {
  RECENT_TRANSACTIONS,
  ACH_BATCHES,
  WIRE_INFLIGHT,
  CARD_AUTHS,
  ACH_RETURN_CODES,
} from '../data/mockData'
import { RiskChip, StatusChip, AgeLabel, RISK_VAR, PageHeader, SectionLabel, TH } from './ui'

const EXTRA = [
  { id:'TXN-88395', from:'ACC-7741', to:'ACC-4471', amount:280000, currency:'USD', rail:'ACH',    risk:'high',     ts:'14:28:22', status:'flagged'   },
  { id:'TXN-88392', from:'ACC-9980', to:'ACC-4471', amount:95000,  currency:'USD', rail:'Wire',   risk:'medium',   ts:'14:27:58', status:'reviewing' },
  { id:'TXN-88388', from:'BNK-3301', to:'EXC-0044', amount:75000,  currency:'USD', rail:'Wire',   risk:'critical', ts:'14:27:30', status:'flagged'   },
  { id:'TXN-88382', from:'MRC-8814', to:'ACC-1129', amount:21000,  currency:'USD', rail:'Card',   risk:'low',      ts:'14:26:55', status:'cleared'   },
  { id:'TXN-88377', from:'EXC-9017', to:'ACC-4471', amount:130000, currency:'USD', rail:'Crypto', risk:'high',     ts:'14:26:20', status:'flagged'   },
]
const ALL_HISTORY = [...RECENT_TRANSACTIONS, ...EXTRA]

const railChip = 'rounded border border-line bg-hover px-1.5 py-px font-mono text-[10px] text-ink-2'
const cellId   = 'px-4 py-2.5 font-mono text-[10.5px] text-ink-3'
const cellAcct = 'px-4 py-2.5 font-mono text-[11px] text-ink-2'
const cellAmt  = 'whitespace-nowrap px-4 py-2.5 font-mono text-xs font-semibold text-ink tnum'
const rowBase  = 'border-b border-line transition-colors duration-150 last:border-b-0 hover:bg-hover'

/* ── ACH batch card with drill-down ── */
function AchBatchCard({ batch, isExpanded, onToggle }) {
  return (
    <div className="glass mb-2.5 overflow-hidden rounded-xl">
      <div
        onClick={onToggle}
        className="flex cursor-pointer items-center gap-3.5 px-4 py-3 transition-colors duration-150 hover:bg-hover"
      >
        <motion.svg
          width="14" height="14" viewBox="0 0 14 14" fill="none"
          className="shrink-0 text-ink-3"
          animate={{ rotate: isExpanded ? 90 : 0 }}
          transition={{ duration: 0.25, ease: [0.32, 0.72, 0, 1] }}
        >
          <path d="M5 3l4 4-4 4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
        </motion.svg>

        <div className="min-w-0 flex-1">
          <div className="mb-0.5 flex items-center gap-2">
            <span className="font-mono text-xs font-bold text-ink">{batch.filename}</span>
            {batch.returnCount > 0 && (
              <span className="rounded-full border border-critical/30 bg-critical/10 px-1.5 py-px text-[10px] font-bold text-critical tnum">
                {batch.returnCount} returns
              </span>
            )}
          </div>
          <div className="text-[11px] text-ink-3 tnum">
            {batch.txnCount.toLocaleString()} transactions ·{' '}
            <span className="font-mono text-ink-2">${(batch.totalAmount / 1000000).toFixed(2)}M</span>
            {' '}· Submitted {batch.submittedAt}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <RiskChip level={batch.risk} />
          <StatusChip status={batch.status} />
        </div>
      </div>

      <AnimatePresence initial={false}>
        {isExpanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: [0.32, 0.72, 0, 1] }}
            className="overflow-hidden"
          >
            <div className="border-t border-line">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="border-b border-line">
                    {['ID', 'From', 'To', 'Amount', 'Return Code', 'Risk', 'Status'].map(h => <TH key={h}>{h}</TH>)}
                  </tr>
                </thead>
                <tbody>
                  {batch.transactions.map(tx => (
                    <tr key={tx.id} className={rowBase}>
                      <td className={cellId}>{tx.id}</td>
                      <td className={cellAcct}>{tx.from}</td>
                      <td className={cellAcct}>{tx.to}</td>
                      <td className={cellAmt}>${tx.amount.toLocaleString()}</td>
                      <td className="px-4 py-2.5">
                        {tx.returnCode ? (
                          <span className="font-mono text-[11px] font-semibold text-critical">
                            {tx.returnCode}
                            <span className="ml-1.5 font-sans font-normal text-ink-3">{ACH_RETURN_CODES[tx.returnCode]}</span>
                          </span>
                        ) : (
                          <span className="text-[11px] text-ink-4">—</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5"><RiskChip level={tx.risk} /></td>
                      <td className="px-4 py-2.5"><StatusChip status={tx.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="border-t border-line bg-hover px-4 py-2 text-[11px] text-ink-3 tnum">
                Showing {batch.transactions.length} of {batch.txnCount} transactions in this batch
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/* ── In-Flight tab ── */
function InFlightTab() {
  const [expandedBatch, setExpandedBatch] = useState(null)

  const summary = [
    { label: 'ACH Batches',         value: `${ACH_BATCHES.length} batches`,  sub: `${ACH_BATCHES.reduce((s, b) => s + b.txnCount, 0).toLocaleString()} transactions` },
    { label: 'Wire Transactions',   value: `${WIRE_INFLIGHT.length} pending`, sub: `$${(WIRE_INFLIGHT.reduce((s, w) => s + w.amount, 0) / 1000000).toFixed(2)}M in-flight` },
    { label: 'Card Authorizations', value: `${CARD_AUTHS.length} open`,       sub: `${CARD_AUTHS.filter(c => c.status === 'stale').length} stale` },
  ]

  return (
    <div className="p-4 pt-5">
      {/* Summary strip */}
      <div className="mb-6 grid grid-cols-3 gap-3">
        {summary.map((s, i) => (
          <motion.div
            key={s.label}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.06, duration: 0.4, ease: [0.32, 0.72, 0, 1] }}
            className="glass rounded-xl px-4 py-3.5"
          >
            <div className="mb-1.5 text-[10px] font-bold uppercase tracking-[0.1em] text-ink-3">{s.label}</div>
            <div className="mb-0.5 font-mono text-lg font-semibold text-ink tnum">{s.value}</div>
            <div className="text-[11px] text-ink-3 tnum">{s.sub}</div>
          </motion.div>
        ))}
      </div>

      {/* ACH */}
      <div className="mb-7">
        <SectionLabel right={<span className="font-mono text-[11px] text-ink-3 tnum">{ACH_BATCHES.length} files</span>}>
          ACH Batches
        </SectionLabel>
        {ACH_BATCHES.map(batch => (
          <AchBatchCard
            key={batch.id}
            batch={batch}
            isExpanded={expandedBatch === batch.id}
            onToggle={() => setExpandedBatch(prev => (prev === batch.id ? null : batch.id))}
          />
        ))}
      </div>

      {/* Wires */}
      <div className="mb-7">
        <SectionLabel right={<span className="font-mono text-[11px] text-ink-3 tnum">{WIRE_INFLIGHT.length} pending</span>}>
          Wire Transactions
        </SectionLabel>
        <div className="glass overflow-hidden rounded-xl">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-line">
                {['Transaction', 'From', 'To', 'Amount', 'SWIFT', 'Submitted', 'Age', 'Risk', 'Status'].map(h => <TH key={h}>{h}</TH>)}
              </tr>
            </thead>
            <tbody>
              {WIRE_INFLIGHT.map(w => (
                <tr key={w.id} className={rowBase}>
                  <td className={cellId}>{w.id}</td>
                  <td className={cellAcct}>{w.from}</td>
                  <td className={cellAcct}>{w.to}</td>
                  <td className={cellAmt}>${w.amount.toLocaleString()}</td>
                  <td className="px-4 py-2.5"><span className={railChip}>{w.swift}</span></td>
                  <td className="px-4 py-2.5 font-mono text-[11px] text-ink-3 tnum">{w.submittedAt}</td>
                  <td className="px-4 py-2.5"><AgeLabel minutes={w.ageMin} warnAfter={30} dangerAfter={90} /></td>
                  <td className="px-4 py-2.5"><RiskChip level={w.risk} /></td>
                  <td className="px-4 py-2.5"><StatusChip status={w.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Card auths */}
      <div>
        <SectionLabel
          right={
            <span className="flex items-center gap-2">
              {CARD_AUTHS.some(c => c.status === 'stale') && (
                <span className="rounded-full border border-high/30 bg-high/10 px-2 py-px text-[10px] font-bold text-high tnum">
                  {CARD_AUTHS.filter(c => c.status === 'stale').length} stale
                </span>
              )}
              <span className="font-mono text-[11px] text-ink-3 tnum">{CARD_AUTHS.length} open</span>
            </span>
          }
        >
          Card Authorizations
        </SectionLabel>
        <div className="glass overflow-hidden rounded-xl">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-line">
                {['Auth ID', 'Merchant', 'Account', 'Amount', 'Network', 'Age', 'Risk', 'Status'].map(h => <TH key={h}>{h}</TH>)}
              </tr>
            </thead>
            <tbody>
              {CARD_AUTHS.map(c => (
                <tr key={c.id} className={`${rowBase} ${c.status === 'stale' ? 'bg-high/5' : ''}`}>
                  <td className={cellId}>{c.id}</td>
                  <td className={cellAcct}>{c.merchant}</td>
                  <td className={cellAcct}>{c.account}</td>
                  <td className={cellAmt}>${c.amount.toLocaleString()}</td>
                  <td className="px-4 py-2.5"><span className={railChip}>{c.network}</span></td>
                  <td className="px-4 py-2.5"><AgeLabel minutes={c.ageMin} warnAfter={60} dangerAfter={200} /></td>
                  <td className="px-4 py-2.5"><RiskChip level={c.risk} /></td>
                  <td className="px-4 py-2.5"><StatusChip status={c.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

/* ── History tab ── */
const HISTORY_COLS = [
  { label: 'Transaction', col: 'id' },
  { label: 'From',        col: 'from' },
  { label: 'To',          col: 'to' },
  { label: 'Amount',      col: 'amount' },
  { label: 'Rail',        col: 'rail' },
  { label: 'Risk',        col: 'risk' },
  { label: 'Status',      col: 'status' },
  { label: 'Time',        col: 'ts' },
]

function HistoryTab() {
  const [search, setSearch] = useState('')
  const [riskFilter, setRiskFilter] = useState('all')
  const [sortBy, setSortBy] = useState('ts')
  const [sortDir, setSortDir] = useState('desc')

  const filtered = ALL_HISTORY
    .filter(tx => {
      if (riskFilter !== 'all' && tx.risk !== riskFilter) return false
      if (search) {
        const s = search.toLowerCase()
        return tx.id.toLowerCase().includes(s) || tx.from.toLowerCase().includes(s) || tx.to.toLowerCase().includes(s)
      }
      return true
    })
    .sort((a, b) => {
      const av = sortBy === 'amount' ? +a[sortBy] : a[sortBy]
      const bv = sortBy === 'amount' ? +b[sortBy] : b[sortBy]
      return sortDir === 'asc' ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1)
    })

  const toggleSort = col => {
    if (sortBy === col) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortBy(col); setSortDir('desc') }
  }

  const counts = ALL_HISTORY.reduce((acc, tx) => { acc[tx.risk] = (acc[tx.risk] || 0) + 1; return acc }, {})
  const chips = [['all', 'All', ALL_HISTORY.length], ['critical', 'Critical', counts.critical || 0], ['high', 'High', counts.high || 0], ['medium', 'Medium', counts.medium || 0], ['low', 'Low', counts.low || 0]]

  return (
    <div className="p-4 pt-5">
      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="glass-soft flex items-center gap-2 rounded-lg px-3 py-2 transition-shadow duration-200 focus-within:ring-1 focus-within:ring-accent/40">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="text-ink-3">
            <circle cx="5" cy="5" r="3.5" stroke="currentColor" strokeWidth="1.3"/>
            <path d="M7.5 7.5l3 3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search ID, account..."
            className="w-[170px] border-none bg-transparent text-xs text-ink outline-none placeholder:text-ink-4"
          />
        </div>

        {chips.map(([f, label, count]) => {
          const isActive = riskFilter === f
          const color = f === 'all' ? 'var(--accent)' : RISK_VAR[f]
          return (
            <button
              key={f}
              onClick={() => setRiskFilter(f)}
              className={`relative flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] transition-colors duration-200
                ${isActive ? 'font-semibold' : 'text-ink-3 hover:text-ink-2'}`}
              style={isActive ? { color } : undefined}
            >
              {isActive && (
                <motion.span
                  layoutId="history-filter-pill"
                  className="glass absolute inset-0 rounded-full"
                  transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                />
              )}
              <span className="relative z-[1]">{label}</span>
              <span className="relative z-[1] font-mono text-[10px] tnum opacity-70">{count}</span>
            </button>
          )
        })}

        <div className="ml-auto text-xs text-ink-2">
          Total:{' '}
          <span className="font-mono font-bold text-ink tnum">
            ${(filtered.reduce((s, tx) => s + tx.amount, 0) / 1000000).toFixed(2)}M
          </span>
        </div>
      </div>

      <div className="glass overflow-hidden rounded-xl">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-line">
                {HISTORY_COLS.map(h => (
                  <TH
                    key={h.col}
                    onClick={() => toggleSort(h.col)}
                    className={`cursor-pointer select-none transition-colors duration-150 hover:text-ink-2 ${sortBy === h.col ? '!text-accent' : ''}`}
                  >
                    {h.label}{' '}
                    {sortBy === h.col
                      ? <span className="text-accent">{sortDir === 'asc' ? '↑' : '↓'}</span>
                      : <span className="text-[9px] text-ink-4">⇅</span>}
                  </TH>
                ))}
              </tr>
            </thead>
            <tbody>
              <AnimatePresence initial={false} mode="popLayout">
                {filtered.map((tx, i) => (
                  <motion.tr
                    key={tx.id}
                    layout
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.22, delay: i * 0.015 }}
                    className={rowBase}
                  >
                    <td className={cellId}>{tx.id}</td>
                    <td className={cellAcct}>{tx.from}</td>
                    <td className={cellAcct}>{tx.to}</td>
                    <td className={cellAmt}>
                      ${tx.amount.toLocaleString()}
                      <span className="ml-1 text-[10px] font-normal text-ink-4">{tx.currency}</span>
                    </td>
                    <td className="px-4 py-2.5"><span className={railChip}>{tx.rail}</span></td>
                    <td className="px-4 py-2.5"><RiskChip level={tx.risk} /></td>
                    <td className="px-4 py-2.5"><StatusChip status={tx.status} /></td>
                    <td className="px-4 py-2.5 font-mono text-[10.5px] text-ink-3 tnum">{tx.ts}</td>
                  </motion.tr>
                ))}
              </AnimatePresence>
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between border-t border-line bg-hover px-4 py-2.5">
          <span className="text-[11px] text-ink-3 tnum">
            Showing <span className="font-mono text-ink-2">{filtered.length}</span> of {ALL_HISTORY.length} settled transactions
          </span>
          <div className="flex gap-1">
            {['←', '1', '2', '3', '→'].map(p => (
              <button
                key={p}
                className={`flex h-[26px] w-[26px] items-center justify-center rounded-lg text-xs transition-colors duration-150
                  ${p === '1'
                    ? 'bg-accent/10 font-semibold text-accent ring-1 ring-accent/25'
                    : 'text-ink-3 hover:bg-hover hover:text-ink-2'}`}
              >
                {p}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

/* ── Main view ── */
const TABS = [
  { id: 'inflight', label: 'In-Flight', badge: ACH_BATCHES.length + WIRE_INFLIGHT.length + CARD_AUTHS.length },
  { id: 'history',  label: 'History',   badge: ALL_HISTORY.length },
]

export default function TransactionsView() {
  const [activeTab, setActiveTab] = useState('inflight')
  const [liveCount, setLiveCount] = useState(88421)

  useEffect(() => {
    const iv = setInterval(() => setLiveCount(n => n + 1), 1400)
    return () => clearInterval(iv)
  }, [])

  const staleCardCount = CARD_AUTHS.filter(c => c.status === 'stale').length
  const returnCount = ACH_BATCHES.reduce((s, b) => s + b.returnCount, 0)

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title="Transactions"
        subtitle={
          <>
            <span className="font-mono text-ink-2 tnum">{liveCount.toLocaleString()}</span> total processed
          </>
        }
      >
        {staleCardCount > 0 && (
          <span className="flex items-center gap-1.5 rounded-lg border border-high/30 bg-high/10 px-3 py-1.5 text-[11px] font-medium text-high">
            <span className="h-[5px] w-[5px] rounded-full bg-high [animation:pulseSoft_2s_ease-in-out_infinite]" />
            {staleCardCount} stale card auth{staleCardCount > 1 ? 's' : ''}
          </span>
        )}
        {returnCount > 0 && (
          <span className="flex items-center gap-1.5 rounded-lg border border-critical/30 bg-critical/10 px-3 py-1.5 text-[11px] font-medium text-critical">
            <span className="h-[5px] w-[5px] rounded-full bg-critical" />
            {returnCount} ACH return{returnCount > 1 ? 's' : ''}
          </span>
        )}
      </PageHeader>

      {/* Tabs */}
      <div className="mx-4 mt-3 flex gap-1">
        {TABS.map(tab => {
          const isActive = activeTab === tab.id
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`relative flex items-center gap-2 rounded-full px-4 py-1.5 text-[13px] tracking-tight transition-colors duration-200
                ${isActive ? 'font-semibold text-accent' : 'text-ink-3 hover:text-ink-2'}`}
            >
              {isActive && (
                <motion.span
                  layoutId="txn-tab-pill"
                  className="absolute inset-0 rounded-full bg-accent/10 ring-1 ring-accent/25"
                  transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                />
              )}
              <span className="relative z-[1]">{tab.label}</span>
              <span className="relative z-[1] font-mono text-[10px] tnum opacity-70">{tab.badge}</span>
            </button>
          )
        })}
      </div>

      <div className="flex-1 overflow-y-auto">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.25, ease: [0.32, 0.72, 0, 1] }}
          >
            {activeTab === 'inflight' ? <InFlightTab /> : <HistoryTab />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}
