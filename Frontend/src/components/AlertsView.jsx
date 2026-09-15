import { useEffect, useMemo, useState } from 'react'
import { LayoutGroup, motion, AnimatePresence } from 'motion/react'
import { RISK_VAR, PageHeader, useToast, Skeleton, ErrorNote, EmptyNote } from './ui'
import { useDataSource } from '../services/DataSourceProvider'
import { useAsync } from '../hooks/useAsync'
import { flagLabel, fmtIso, shortId } from '../lib/format'

const LEVELS = ['all', 'critical', 'high', 'medium', 'low']
const TYPES = ['ALL', 'AGGREGATE', 'COMMUNITY', 'CYCLE', 'LIVE_GNN']
const PAGE = 50

const ACTIONS = [
  { label: 'Escalate',       status: 'escalated', tone: 'text-critical hover:opacity-80' },
  { label: 'Mark reviewed',  status: 'reviewed',  tone: 'text-high hover:opacity-80' },
  { label: 'Dismiss',        status: 'dismissed', tone: 'text-ink-3 hover:text-ink-2' },
]

function AlertRow({ alert, isOpen, onToggle, onAction, onOpenGraph }) {
  const color = RISK_VAR[alert.risk_level]
  const signals = Object.entries(alert.details?.signals || {}).filter(([, v]) => v).map(([k]) => k)
  return (
    <article className="py-5">
      <button type="button" onClick={onToggle} className="w-full cursor-pointer text-left">
        <div className="flex items-start gap-3">
          <span className="mt-2 h-[6px] w-[6px] shrink-0 rounded-full" style={{ background: color }} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
              <span className="text-[14px] font-semibold text-ink">{flagLabel(alert.flag_type)}</span>
              <span className="text-[11px] font-bold uppercase tracking-[0.06em]" style={{ color }}>{alert.risk_level}</span>
              <span className="font-mono text-[11px] text-ink-4">{fmtIso(alert.last_detected_at)}</span>
              {alert.status !== 'open' && <span className="font-mono text-[10px] uppercase text-ink-4">{alert.status}</span>}
            </div>
            <p className="mt-1.5 text-[14px] leading-relaxed text-ink-2">{alert.explanation}</p>
            <p className="mt-2 font-mono text-[12px] text-ink-3 tnum">
              {shortId(alert.primary_account)}{alert.account_ids.length > 1 ? ` +${alert.account_ids.length - 1} accounts` : ''}
              {' · '}score {(alert.risk_score * 100).toFixed(0)}%{alert.detection_count > 1 ? ` · seen ${alert.detection_count}×` : ''}
            </p>
          </div>
        </div>
      </button>

      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }} className="overflow-hidden">
            <div className="ml-[18px] mt-4 max-w-3xl">
              <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">
                Signals · <span className="font-mono font-normal normal-case">#{alert.id}</span>
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {signals.length === 0 && <span className="text-[12px] text-ink-4">no structured signals recorded</span>}
                {signals.map(s => <span key={s} className="rounded bg-hover px-2 py-0.5 font-mono text-[11px] text-ink-2">{s}</span>)}
                {alert.details?.gnn_score != null && <span className="rounded bg-hover px-2 py-0.5 font-mono text-[11px] text-ink-2">gnn {Number(alert.details.gnn_score).toFixed(3)}</span>}
                {alert.details?.community_id && <span className="rounded bg-hover px-2 py-0.5 font-mono text-[11px] text-ink-2">community {alert.details.community_id}</span>}
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-5">
                {ACTIONS.map(a => (
                  <button key={a.status} type="button" onClick={e => { e.stopPropagation(); onAction(alert, a.status) }}
                    className={`text-[13px] font-medium ${a.tone}`}>{a.label}</button>
                ))}
                <button type="button" onClick={e => { e.stopPropagation(); onOpenGraph(alert.primary_account) }}
                  className="ml-auto text-[13px] font-medium text-accent hover:opacity-70">Open in Graph →</button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </article>
  )
}

export default function AlertsView({ onNav, params }) {
  const { source, mode } = useDataSource()
  const [level, setLevel] = useState('all')
  const [type, setType] = useState('ALL')
  const [page, setPage] = useState(0)
  const [openId, setOpenId] = useState(params?.id ? Number(params.id) : null)
  const [local, setLocal] = useState({})            // id → status after an action
  const { show, ToastHost } = useToast()

  const query = useMemo(() => ({
    flag_type: type === 'ALL' ? undefined : type,
    min_level: level === 'all' ? 'low' : level,
    limit: PAGE, offset: page * PAGE,
  }), [type, level, page])
  const q = useAsync(() => source.alerts.list(query), [source.mode, query])

  useEffect(() => {
    if (!params?.id) return
    const t = window.setTimeout(() => document.getElementById(`alert-${params.id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 150)
    return () => window.clearTimeout(t)
  }, [params?.id, q.data])

  const items = (q.data?.items ?? []).filter(a => level === 'all' || a.risk_level === level)
  const total = q.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE))

  const act = async (alert, status) => {
    try {
      await source.alerts.setStatus(alert.id, status)
      setLocal(l => ({ ...l, [alert.id]: status }))
      show(mode === 'demo' ? `Marked ${status} (demo — not persisted)` : `Alert #${alert.id} marked ${status}`)
    } catch (e) {
      show(e?.message || 'Update failed')
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader title="Risk Alerts" subtitle="Every flag carries a written reason — a regulatory requirement, not a nicety">
        <span className="font-mono text-[12px] text-ink-3 tnum">{total.toLocaleString()} open</span>
      </PageHeader>

      <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-2 px-8 pb-4">
        {LEVELS.map(f => (
          <button key={f} type="button" onClick={() => { setLevel(f); setPage(0) }}
            className={`text-[13px] capitalize transition-colors ${level === f ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}
            style={level === f && f !== 'all' ? { color: RISK_VAR[f] } : undefined}>{f}</button>
        ))}
        <span className="text-ink-4">·</span>
        {TYPES.map(t => (
          <button key={t} type="button" onClick={() => { setType(t); setPage(0) }}
            className={`text-[12px] transition-colors ${type === t ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}>
            {t === 'ALL' ? 'All types' : flagLabel(t)}
          </button>
        ))}
        <span className="ml-auto text-[12px] text-ink-4 tnum">page {page + 1} of {pages}</span>
      </div>

      <div className="flex-1 overflow-y-auto px-8">
        {q.error ? <ErrorNote error={q.error} onRetry={q.reload} />
          : q.loading ? <div className="space-y-4 py-4"><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /></div>
          : items.length === 0 ? <EmptyNote>No alerts match these filters.</EmptyNote> : (
          <LayoutGroup>
            <motion.div layout className="divide-y divide-line/70">
              {items.map(alert => (
                <div key={alert.id} id={`alert-${alert.id}`}>
                  <AlertRow alert={{ ...alert, status: local[alert.id] ?? alert.status }} isOpen={openId === alert.id}
                    onToggle={() => setOpenId(prev => (prev === alert.id ? null : alert.id))}
                    onAction={act} onOpenGraph={(acct) => onNav('graph', { account: acct })} />
                </div>
              ))}
            </motion.div>
          </LayoutGroup>
        )}
        <div className="flex items-center justify-end gap-3 py-4 text-[12px]">
          <button type="button" disabled={page === 0} onClick={() => setPage(p => p - 1)} className="text-ink-3 hover:text-ink disabled:opacity-40">← Newer</button>
          <button type="button" disabled={page + 1 >= pages} onClick={() => setPage(p => p + 1)} className="text-ink-3 hover:text-ink disabled:opacity-40">Older →</button>
        </div>
      </div>
      <ToastHost />
    </div>
  )
}
