import { useEffect, useMemo, useState } from 'react'
import { GraphCanvas } from './GraphCanvas'
import PrCurve from './PrCurve'
import { PageHeader, RiskChip, Skeleton, ErrorNote, DemoNote, EmptyNote, useToast } from './ui'
import { useDataSource } from '../services/DataSourceProvider'
import { useAsync } from '../hooks/useAsync'
import { NotInSnapshotError } from '../services/dataSource'
import { fmtIso, shortId } from '../lib/format'

const LENSES = [['gnn', 'GNN heat'], ['pagerank', 'PageRank'], ['community', 'Louvain'], ['marked', 'Marked vs dataset']]
const STAGES = ['pagerank', 'louvain', 'cycle', 'gnn', 'aggregate']

function RunControls({ source, mode, onDone }) {
  const latest = useAsync(() => source.pipeline.latestRun(), [source.mode])
  const [run, setRun] = useState(null)
  const { show, ToastHost } = useToast()

  useEffect(() => {
    if (!run || run.status === 'completed' || run.status === 'failed') return
    const t = window.setInterval(async () => {
      try {
        const s = await source.pipeline.runStatus(run.id)
        setRun(s)
        if (s.status === 'completed' || s.status === 'failed') { window.clearInterval(t); onDone() }
      } catch { /* keep polling */ }
    }, 1500)
    return () => window.clearInterval(t)
  }, [run, source, onDone])

  const start = async () => {
    try { const { run_id } = await source.pipeline.run(); setRun({ id: run_id, status: 'queued', progress: 0 }) }
    catch (e) { show(e?.response?.status === 409 ? 'A run is already active' : e?.message || 'Could not start') }
  }
  const r = run || latest.data
  const pct = Math.round(((r?.progress) || 0) * 100)
  return (
    <div className="flex items-center gap-4">
      {r && r.status !== 'none' && (
        <div className="flex items-center gap-3 text-[12px] text-ink-3">
          <span className="font-mono">{r.status}{r.stage ? ` · ${r.stage}` : ''}</span>
          {r.status === 'running' || r.status === 'queued' ? <span className="h-1 w-32 overflow-hidden rounded bg-hover"><span className="block h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></span>
            : r.finished_at ? <span className="font-mono text-[11px] text-ink-4">{fmtIso(r.finished_at)}</span> : null}
          {r.status === 'failed' && <span className="text-critical">{r.error}</span>}
        </div>
      )}
      {mode === 'live'
        ? <button type="button" onClick={start} disabled={r && (r.status === 'running' || r.status === 'queued')} className="text-[13px] font-medium text-accent hover:opacity-70 disabled:opacity-40">Run pipeline →</button>
        : <span className="text-[11px] text-ink-4">precomputed run (demo)</span>}
      <div className="flex gap-1">{STAGES.map(s => <span key={s} className={`font-mono text-[9px] uppercase ${r?.stage === s ? 'text-accent' : 'text-ink-4'}`}>{s}</span>)}</div>
      <ToastHost />
    </div>
  )
}

export default function PipelineView({ onNav }) {
  const { source, mode } = useDataSource()
  const [lens, setLens] = useState('gnn')
  const [cap, setCap] = useState(600)
  const [cutoff, setCutoff] = useState(null)
  const [selected, setSelected] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)

  const overview = useAsync(() => source.stats.overview(), [source.mode, reloadKey])
  const threshold = useAsync(() => source.pipeline.threshold(), [source.mode, reloadKey])
  const curve = useAsync(() => source.pipeline.metricsCurve(46), [source.mode, reloadKey])
  const metricLens = lens === 'pagerank' || lens === 'community' ? 'pagerank' : 'gnn'
  const graph = useAsync(() => source.pipeline.overview(metricLens, cap), [source.mode, metricLens, cap, reloadKey])
  const communities = useAsync(() => source.pipeline.communities({ sort: 'risk', limit: 30 }), [source.mode, reloadKey])
  const marked = useAsync(() => source.pipeline.marked({ limit: 50 }), [source.mode, reloadKey])
  const effective = cutoff ?? threshold.data?.default ?? 0.74
  const metrics = useAsync(() => (curve.data?.loaded ? source.pipeline.metrics(effective) : Promise.resolve(null)), [source.mode, effective, curve.data?.loaded])

  const labelled = Boolean(overview.data?.dataset?.labelled)
  const elements = useMemo(() => graph.data || { nodes: [], edges: [] }, [graph.data])
  const total = graph.data?.truncated?.total

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader title="Pipeline" subtitle="PageRank → Louvain → cycle detection → GNN inference → risk aggregation, on the real graph">
        <RunControls source={source} mode={mode} onDone={() => setReloadKey(k => k + 1)} />
      </PageHeader>

      <div className="flex min-h-0 flex-1 gap-0 border-t border-line">
        {/* left: threshold + tables */}
        <div className="flex w-[360px] shrink-0 flex-col gap-6 overflow-y-auto border-r border-line px-6 py-5">
          <section>
            <div className="flex items-baseline justify-between">
              <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">GNN mark cutoff</p>
              <span className="font-mono text-[12px] text-ink tnum">{effective.toFixed(2)}</span>
            </div>
            <input type="range" min={threshold.data?.min ?? 0.5} max={threshold.data?.max ?? 0.95} step="0.01" value={effective}
              onChange={e => setCutoff(Number(e.target.value))} className="mt-2 w-full accent-[var(--accent)]" />
            {threshold.data && <p className="mt-1 text-[10.5px] text-ink-4">model's tuned value {threshold.data.default.toFixed(3)} · <button type="button" onClick={() => setCutoff(null)} className="text-accent">reset</button></p>}
            {!labelled ? <p className="mt-3 text-[11px] text-ink-4">This dataset has no ground-truth labels, so precision/recall are unavailable.</p>
              : metrics.data?.loaded ? (
              <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[12px]">
                <span className="text-ink-3">precision</span><span className="font-mono text-ink tnum">{(metrics.data.precision * 100).toFixed(0)}%</span>
                <span className="text-ink-3">recall</span><span className="font-mono text-ink tnum">{(metrics.data.recall * 100).toFixed(0)}%</span>
                <span className="text-ink-3">caught</span><span className="font-mono text-ink tnum">{metrics.data.tp.toLocaleString()}</span>
                <span className="text-ink-3">false positives</span><span className="font-mono text-critical tnum">{metrics.data.fp.toLocaleString()}</span>
                <span className="text-ink-3">marked</span><span className="font-mono text-ink tnum">{metrics.data.marked.toLocaleString()}</span>
              </div>
            ) : metrics.loading ? <Skeleton className="mt-3 h-16 w-full" /> : null}
            {labelled && curve.data?.loaded && (
              <div className="mt-3">
                <PrCurve curve={curve.data} cutoff={effective} />
                <div className="flex gap-4 text-[10px] text-ink-4"><span><span className="mr-1 inline-block h-0.5 w-3 bg-accent align-middle" />precision</span><span><span className="mr-1 inline-block h-0.5 w-3 bg-high align-middle" />recall</span></div>
              </div>
            )}
          </section>

          <section>
            <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">Riskiest communities</p>
            {communities.error ? <ErrorNote error={communities.error} /> : communities.loading ? <Skeleton className="mt-2 h-24 w-full" /> : (
              <div className="mt-2 divide-y divide-line/70">
                {communities.data.map(c => (
                  <div key={c.community_id} className="flex items-center gap-3 py-1.5 text-[12px]">
                    <span className="font-mono text-ink-2">{shortId(c.community_id)}</span>
                    <span className="text-ink-4 tnum">{c.size} accts</span>
                    <span className="ml-auto font-mono text-ink tnum">{(c.risk_score * 100).toFixed(0)}%</span>
                    <RiskChip level={c.risk_tier} />
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">Marked accounts</p>
            {marked.error ? <ErrorNote error={marked.error} /> : marked.loading ? <Skeleton className="mt-2 h-24 w-full" /> : marked.data.length === 0 ? <EmptyNote>No marks yet — run the pipeline.</EmptyNote> : (
              <div className="mt-2 divide-y divide-line/70">
                {marked.data.map(m => (
                  <button key={m.account_id} type="button" onClick={() => onNav('graph', { account: m.account_id })} className="flex w-full items-center gap-3 py-1.5 text-left text-[12px] hover:bg-hover/60">
                    <span className="font-mono text-ink-2">{shortId(m.account_id)}</span>
                    <span className="font-mono text-[10px] text-ink-4">{Object.entries(m.signals || {}).filter(([, v]) => v).map(([k]) => k).join(' · ')}</span>
                    <span className="ml-auto font-mono text-ink tnum">{((m.combined_score || 0) * 100).toFixed(0)}%</span>
                  </button>
                ))}
              </div>
            )}
          </section>
        </div>

        {/* right: graph */}
        <div className="relative min-w-0 flex-1">
          <div className="flex items-center gap-4 px-6 py-3">
            {LENSES.filter(([id]) => id !== 'marked' || labelled).map(([id, label]) => (
              <button key={id} type="button" onClick={() => setLens(id)} className={`text-[12px] ${lens === id ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}>{label}</button>
            ))}
            <label className="ml-auto flex items-center gap-2 text-[11px] text-ink-4">
              nodes <input type="range" min="100" max="2000" step="100" value={cap} onChange={e => setCap(Number(e.target.value))} disabled={mode === 'demo'} className="w-24 accent-[var(--accent)]" /> <span className="font-mono tnum">{cap}</span>
            </label>
            {graph.data && <span className="font-mono text-[11px] text-ink-4 tnum">{elements.nodes.length.toLocaleString()}{total ? ` of ${total.toLocaleString()}` : ''} accounts</span>}
          </div>
          <div className="absolute inset-x-0 bottom-0 top-12 border-t border-line">
            {graph.error instanceof NotInSnapshotError ? <div className="p-6"><DemoNote>{graph.error.message}</DemoNote></div>
              : graph.error ? <div className="p-6"><ErrorNote error={graph.error} onRetry={graph.reload} /></div>
              : graph.loading ? <div className="p-6"><Skeleton className="h-full min-h-[300px] w-full" /></div>
              : <GraphCanvas elements={elements} lens={lens} cutoff={effective} selectedId={selected?.id} onSelectNode={setSelected} />}
            {selected && (
              <div className="glass absolute right-4 top-4 w-[260px] rounded-lg p-4 text-[12px]">
                <p className="break-all font-mono text-ink">{selected.id}</p>
                <p className="mt-1 text-ink-3">GNN {selected.gnn_risk_score != null ? Number(selected.gnn_risk_score).toFixed(3) : '—'} · {selected.gnn_risk_tier ?? '—'}{selected.in_cycle ? ' · on cycle' : ''}{selected.truth ? ` · dataset: ${selected.truth_typology}` : ''}</p>
                <button type="button" onClick={() => onNav('graph', { account: selected.id })} className="mt-2 font-medium text-accent hover:opacity-70">Open in Graph →</button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
