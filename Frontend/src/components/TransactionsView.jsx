import { useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { RiskChip, StatusChip, RISK_VAR, PageHeader, TH, Skeleton, ErrorNote, EmptyNote, DemoNote } from './ui'
import { useDataSource } from '../services/DataSourceProvider'
import { useAsync } from '../hooks/useAsync'
import { NotInSnapshotError } from '../services/dataSource'
import { fmtAmount, fmtTs, shortId, fmtCompact } from '../lib/format'

const COLS = [
  { label: 'Transaction', col: 'txn_id' },
  { label: 'From',        col: 'sender_id' },
  { label: 'To',          col: 'receiver_id' },
  { label: 'Amount',      col: 'amount_cents' },
  { label: 'Currency',    col: 'currency' },
  { label: 'Risk',        col: 'risk_tier' },
  { label: 'Status',      col: 'flagged' },
  { label: 'Time',        col: 'ts' },
]
const TIER_RANK = { low: 0, medium: 1, high: 2, critical: 3 }
const cellId   = 'px-4 py-2.5 font-mono text-[10.5px] text-ink-3 first:pl-7'
const cellAcct = 'px-4 py-2.5 font-mono text-[11px] text-ink-2'
const cellAmt  = 'whitespace-nowrap px-4 py-2.5 font-mono text-xs font-semibold text-ink tnum'
const rowBase  = 'transition-colors duration-150 hover:bg-hover'

export default function TransactionsView({ onNav, params }) {
  const { source, mode } = useDataSource()
  const [account, setAccount] = useState(params?.account ?? '')
  const [submitted, setSubmitted] = useState(params?.account ?? '')
  const [currency, setCurrency] = useState('')
  const [risk, setRisk] = useState('all')
  const [sortBy, setSortBy] = useState('ts')
  const [sortDir, setSortDir] = useState('desc')

  const overview = useAsync(() => source.stats.overview(), [source.mode])
  const q = useAsync(
    () => source.transactions.list({ limit: 200, account_id: submitted || undefined, currency: currency || undefined }),
    [source.mode, submitted, currency],
  )

  const rows = useMemo(() => {
    const items = (q.data?.items ?? []).filter(t => risk === 'all' || t.risk_tier === risk)
    const dir = sortDir === 'asc' ? 1 : -1
    return [...items].sort((a, b) => {
      const va = sortBy === 'risk_tier' ? TIER_RANK[a.risk_tier] : a[sortBy]
      const vb = sortBy === 'risk_tier' ? TIER_RANK[b.risk_tier] : b[sortBy]
      return (va > vb ? 1 : va < vb ? -1 : 0) * dir
    })
  }, [q.data, risk, sortBy, sortDir])

  const toggleSort = (col) => {
    if (sortBy === col) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortBy(col); setSortDir('desc') }
  }
  const counts = (q.data?.items ?? []).reduce((acc, t) => { acc[t.risk_tier] = (acc[t.risk_tier] || 0) + 1; return acc }, {})
  const currencies = overview.data?.currencies ?? []

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader title="Transactions"
        subtitle={<><span className="font-mono text-ink-2 tnum">{fmtCompact(overview.data?.transactions ?? 0)}</span> settled transfers in the dataset</>}>
        <form onSubmit={e => { e.preventDefault(); setSubmitted(account.trim()); onNav('transactions', { account: account.trim() }) }}
          className="flex items-center gap-2 border-b border-line py-1 focus-within:border-line-2">
          <input value={account} onChange={e => setAccount(e.target.value)} placeholder="Account id…"
            className="w-64 bg-transparent font-mono text-xs text-ink outline-none placeholder:text-ink-4" />
          {submitted && <button type="button" onClick={() => { setAccount(''); setSubmitted(''); onNav('transactions') }} className="text-[11px] text-ink-3 hover:text-ink">clear</button>}
        </form>
      </PageHeader>

      {/* by-currency strip */}
      <div className="flex shrink-0 flex-wrap gap-x-6 gap-y-1 px-8 pb-3">
        <button type="button" onClick={() => setCurrency('')} className={`text-[12px] ${currency === '' ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}>All currencies</button>
        {currencies.slice(0, 8).map(c => (
          <button key={c.code} type="button" onClick={() => setCurrency(c.code)}
            className={`text-[12px] tnum ${currency === c.code ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}>
            {c.code} <span className="font-mono text-[10px] text-ink-4">{fmtCompact(c.tx_count)}</span>
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2 px-8 py-2.5">
        {[['all', 'All'], ['critical', 'Critical'], ['high', 'High'], ['medium', 'Medium'], ['low', 'Low']].map(([f, label]) => (
          <button key={f} onClick={() => setRisk(f)}
            className={`relative flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] transition-colors ${risk === f ? 'font-semibold' : 'text-ink-3 hover:text-ink-2'}`}
            style={risk === f ? { color: f === 'all' ? 'var(--ink)' : RISK_VAR[f] } : undefined}>
            {risk === f && <motion.span layoutId="history-filter-pill" className="absolute inset-0 rounded-full bg-hover" transition={{ type: 'spring', stiffness: 420, damping: 34 }} />}
            <span className="relative z-[1]">{label}</span>
            <span className="relative z-[1] font-mono text-[10px] tnum opacity-70">{f === 'all' ? (q.data?.items.length ?? 0) : (counts[f] || 0)}</span>
          </button>
        ))}
        {submitted && <span className="ml-auto font-mono text-[11px] text-ink-3">account {shortId(submitted)} · both directions</span>}
      </div>

      <div className="flex-1 overflow-y-auto">
        {q.error instanceof NotInSnapshotError ? <div className="px-8 py-4"><DemoNote>{q.error.message}</DemoNote></div>
          : q.error ? <div className="px-8 py-4"><ErrorNote error={q.error} onRetry={q.reload} /></div>
          : q.loading ? <div className="space-y-2 px-8 py-4"><Skeleton className="h-8 w-full" /><Skeleton className="h-8 w-full" /><Skeleton className="h-8 w-full" /></div>
          : rows.length === 0 ? <EmptyNote>No transactions match.</EmptyNote> : (
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-line">
                {COLS.map((h, i) => (
                  <TH key={h.col} onClick={() => toggleSort(h.col)}
                    className={`cursor-pointer select-none transition-colors hover:text-ink-2 ${sortBy === h.col ? '!text-accent' : ''} ${i === 0 ? 'pl-7' : ''}`}>
                    {h.label}{' '}
                    {sortBy === h.col ? <span className="text-accent">{sortDir === 'asc' ? '↑' : '↓'}</span> : <span className="text-[9px] text-ink-4">⇅</span>}
                  </TH>
                ))}
              </tr>
            </thead>
            <tbody>
              <AnimatePresence initial={false} mode="popLayout">
                {rows.map((tx, i) => (
                  <motion.tr key={tx.txn_id} layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, transition: { duration: 0.12 } }} transition={{ duration: 0.3, delay: Math.min(i, 20) * 0.015 }} className={rowBase}>
                    <td className={cellId}>{shortId(tx.txn_id)}</td>
                    <td className={cellAcct}><button type="button" onClick={() => onNav('graph', { account: tx.sender_id })} className="hover:text-accent">{shortId(tx.sender_id)}</button></td>
                    <td className={cellAcct}><button type="button" onClick={() => onNav('graph', { account: tx.receiver_id })} className="hover:text-accent">{shortId(tx.receiver_id)}</button></td>
                    <td className={cellAmt}>{fmtAmount(tx.amount_cents)}</td>
                    <td className="px-4 py-2.5 font-mono text-[10px] text-ink-3">{tx.currency}</td>
                    <td className="px-4 py-2.5"><RiskChip level={tx.risk_tier} /></td>
                    <td className="px-4 py-2.5"><StatusChip status={tx.flagged ? 'flagged' : 'settled'} /></td>
                    <td className="px-4 py-2.5 font-mono text-[10.5px] text-ink-3 tnum">{fmtTs(tx.ts)}</td>
                  </motion.tr>
                ))}
              </AnimatePresence>
            </tbody>
          </table>
        )}
        {mode === 'demo' && !submitted && q.data && (
          <p className="px-8 py-3 text-[11px] text-ink-4">Demo snapshot: the {q.data.items.length} most recent transfers. Per-account history is available for featured accounts.</p>
        )}
      </div>
    </div>
  )
}
