import { useState } from 'react'
import { RiskChip, Skeleton, ErrorNote, DemoNote } from './ui'
import { useAsync } from '../hooks/useAsync'
import { NotInSnapshotError } from '../services/dataSource'
import { shortId } from '../lib/format'

function Row({ k, v, mono = true }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5 text-[12px]">
      <span className="text-ink-3">{k}</span>
      <span className={`${mono ? 'font-mono tnum' : ''} text-right text-ink`}>{v ?? '—'}</span>
    </div>
  )
}

export function InspectorSidebar({ source, node, onClose, onExplore }) {
  const [whatIf, setWhatIf] = useState(null)
  const [whatIfError, setWhatIfError] = useState(null)
  const explain = useAsync(() => (node ? source.ai.explain(node.id) : Promise.resolve(null)), [source.mode, node?.id])
  if (!node) return null

  const simulateCycle = async () => {
    setWhatIfError(null)
    try { setWhatIf(await source.risk.evaluate(node.id, { gnn_score: node.gnn_risk_score ?? 0.5, has_cycle: true, cycle_length: 3 })) }
    catch (e) { setWhatIfError(e) }
  }
  const ex = explain.data

  return (
    <aside className="glass-soft flex h-full w-[380px] shrink-0 flex-col overflow-y-auto border-l border-line px-6 py-5">
      <div className="flex items-start justify-between gap-3 border-b border-line pb-4">
        <div className="min-w-0">
          <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">Account</p>
          <p className="mt-1 break-all font-mono text-[12px] text-ink">{node.id}</p>
        </div>
        <button type="button" onClick={onClose} className="text-ink-4 hover:text-ink" aria-label="Close">✕</button>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <span className="font-mono text-[28px] font-semibold leading-none text-ink tnum">{((node.gnn_risk_score ?? node.risk_score ?? 0) * 100).toFixed(0)}%</span>
        <div className="flex flex-col gap-1">
          <RiskChip level={node.gnn_risk_tier || node.risk_tier || 'low'} />
          <span className="text-[10px] text-ink-4">GNN risk score</span>
        </div>
      </div>

      <div className="mt-4 divide-y divide-line/70">
        <Row k="On detected cycle" v={node.in_cycle ? 'yes' : 'no'} />
        <Row k="Marked by pipeline" v={node.marked ? 'yes' : 'no'} />
        <Row k="Community" v={node.community_id ? shortId(node.community_id) : '—'} />
        <Row k="PageRank" v={node.pagerank_score ? Number(node.pagerank_score).toExponential(2) : '—'} />
        {node.risk_score != null && node.gnn_risk_score != null && node.risk_score !== node.gnn_risk_score && (
          <Row k="Aggregated verdict" v={`${(node.risk_score * 100).toFixed(0)}% · ${node.risk_tier}`} />
        )}
      </div>

      <div className="mt-5 border-t border-line pt-4">
        <div className="flex items-center justify-between">
          <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">Explanation</p>
          {ex && <span className="font-mono text-[10px] text-ink-4">{ex.provider === 'none' ? 'rule-based' : `${ex.provider}${ex.model ? ` · ${ex.model}` : ''}`}</span>}
        </div>
        {explain.loading ? <div className="mt-2 space-y-2"><Skeleton className="h-3 w-full" /><Skeleton className="h-3 w-5/6" /></div>
          : explain.error instanceof NotInSnapshotError ? <div className="mt-2"><DemoNote>{explain.error.message}</DemoNote></div>
          : explain.error ? <div className="mt-2"><ErrorNote error={explain.error} onRetry={explain.reload} /></div>
          : ex ? (
            <div className="mt-2 space-y-2">
              <div className="flex items-center gap-2 text-[11px] text-ink-3">
                <RiskChip level={ex.risk_level} /><span className="font-mono tnum">confidence {(ex.confidence * 100).toFixed(0)}%</span>
                {ex.detected_typology && <span className="rounded bg-hover px-1.5 py-px font-mono text-[10px] text-ink-2">{ex.detected_typology}</span>}
              </div>
              <p className="text-[13px] leading-relaxed text-ink-2">{ex.explanation}</p>
              <p className="font-mono text-[10.5px] leading-relaxed text-ink-4">{ex.compliance_summary}</p>
            </div>
          ) : null}
      </div>

      <div className="mt-5 border-t border-line pt-4">
        <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">What-if</p>
        <button type="button" onClick={simulateCycle} className="mt-2 text-[12px] font-medium text-accent hover:opacity-70">
          Re-evaluate as if on a 3-hop cycle →
        </button>
        {whatIfError instanceof NotInSnapshotError ? <div className="mt-2"><DemoNote>{whatIfError.message}</DemoNote></div>
          : whatIfError ? <div className="mt-2"><ErrorNote error={whatIfError} /></div> : null}
        {whatIf && (
          <div className="mt-2 text-[12px] text-ink-2">
            <div className="flex items-center gap-2"><RiskChip level={whatIf.risk_tier} /><span className="font-mono tnum">{(whatIf.risk_score * 100).toFixed(0)}% · confidence {(whatIf.confidence * 100).toFixed(0)}%</span></div>
            <p className="mt-1 text-ink-3">{whatIf.explanation}</p>
          </div>
        )}
      </div>

      <button type="button" onClick={() => onExplore(node.id)} className="mt-6 text-[12px] font-medium text-accent hover:opacity-70">Center on this account →</button>
    </aside>
  )
}
