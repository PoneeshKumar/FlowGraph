import { useEffect, useState } from 'react'
import { PageHeader, ErrorNote, DemoNote, Skeleton } from './ui'
import { useDataSource } from '../services/DataSourceProvider'
import { useAsync } from '../hooks/useAsync'
import { fmtDay } from '../lib/format'

const STAGES = ['reset', 'ingest', 'pagerank', 'louvain', 'cycle', 'gnn', 'aggregate']

export default function UploadView({ onNav }) {
  const { source, mode } = useDataSource()
  const current = useAsync(() => source.datasets.current(), [source.mode])
  const [transactions, setTransactions] = useState(null)
  const [patterns, setPatterns] = useState(null)
  const [confirmed, setConfirmed] = useState(false)
  const [progress, setProgress] = useState(null)      // upload progress 0..1
  const [run, setRun] = useState(null)                // pipeline_runs row
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!run || run.status === 'completed' || run.status === 'failed') return
    const t = window.setInterval(async () => {
      try { const s = await source.pipeline.runStatus(run.id); setRun(s) } catch { /* keep polling */ }
    }, 1500)
    return () => window.clearInterval(t)
  }, [run, source])

  const submit = async (e) => {
    e.preventDefault()
    if (!transactions || !confirmed) return
    setError(null); setProgress(0)
    try {
      const { run_id } = await source.datasets.upload({ transactions, patterns }, setProgress)
      setProgress(null)
      setRun({ id: run_id, status: 'queued', progress: 0, stage: null })
    } catch (err) { setProgress(null); setError(err) }
  }

  if (mode !== 'live') {
    return (
      <div className="px-8 pt-4">
        <PageHeader title="Analyze a file" subtitle="Run the whole pipeline on your own transactions" />
        <DemoNote>Uploading a dataset needs a running backend — the demo is a static snapshot.</DemoNote>
      </div>
    )
  }

  const pct = Math.round(((run?.progress) || 0) * 100)
  const ds = current.data
  const busy = progress != null || (run && run.status !== 'completed' && run.status !== 'failed')
  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <PageHeader title="Analyze a file" subtitle="Replace the loaded graph with your transactions and run PageRank → Louvain → cycles → GNN → aggregation">
        {current.loading ? <Skeleton className="h-4 w-40" /> : ds ? (
          <span className="text-[12px] text-ink-3">
            Loaded now: <span className="font-mono text-ink">{ds.name}</span>
            {ds.start_ts ? ` · ${fmtDay(ds.start_ts)} – ${fmtDay(ds.end_ts)}` : ''}{ds.labelled ? ' · labelled' : ' · unlabelled'}
          </span>
        ) : null}
      </PageHeader>

      <form onSubmit={submit} className="max-w-2xl space-y-6 px-8 pb-10">
        <section className="space-y-2">
          <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">1 · Transactions CSV (required)</p>
          <p className="text-[12px] text-ink-3">
            IBM AML layout, in this column order: <span className="font-mono text-[11px]">Timestamp, From Bank, Account, To Bank, Account, Amount Received, Receiving Currency, Amount Paid, Payment Currency, Payment Format, Is Laundering</span>.
            {' '}<a href={source.datasets.templateUrl()} className="font-medium text-accent hover:opacity-70">Download a template →</a>
          </p>
          <input type="file" accept=".csv,text/csv" onChange={e => setTransactions(e.target.files?.[0] ?? null)} className="text-[12px] text-ink-2" />
        </section>

        <section className="space-y-2">
          <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">2 · Patterns file (optional)</p>
          <p className="text-[12px] text-ink-3">An IBM-style <span className="font-mono text-[11px]">*_Patterns.txt</span> with known laundering attempts. With it, the Pipeline view can show precision and recall; without it the engine still scores every account.</p>
          <input type="file" accept=".txt,text/plain" onChange={e => setPatterns(e.target.files?.[0] ?? null)} className="text-[12px] text-ink-2" />
        </section>

        <section className="space-y-3 rounded-md border border-line-2 px-4 py-3">
          <label className="flex items-start gap-3 text-[12px] text-ink-2">
            <input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} className="mt-0.5 accent-[var(--accent)]" />
            <span>I understand this <strong>replaces the currently loaded graph</strong> (Neo4j, Redis and all risk flags). To get the IBM demo back, re-run the ingest described in the README.</span>
          </label>
          <button type="submit" disabled={!transactions || !confirmed || busy}
            className="text-[13px] font-medium text-accent hover:opacity-70 disabled:opacity-40">
            {progress != null ? `Uploading… ${Math.round(progress * 100)}%` : 'Upload and run the pipeline →'}
          </button>
          {error && <ErrorNote error={error} />}
        </section>

        {run && (
          <section className="space-y-2">
            <div className="flex items-center gap-3 text-[12px] text-ink-3">
              <span className="font-mono">{run.status}{run.stage ? ` · ${run.stage}` : ''}</span>
              <span className="h-1 w-40 overflow-hidden rounded bg-hover"><span className="block h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></span>
              <span className="font-mono text-[11px] tnum">{pct}%</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {STAGES.map(s => <span key={s} className={`font-mono text-[10px] uppercase ${run.stage === s ? 'text-accent' : 'text-ink-4'}`}>{s}</span>)}
            </div>
            {run.status === 'failed' && <ErrorNote error={{ message: `Failed at ${run.stage}: ${run.error}` }} />}
            {run.status === 'completed' && (
              <p className="text-[12px] text-ink-2">Done — {run.counts?.gnn_scored?.toLocaleString?.() ?? ''} accounts scored, {run.counts?.marked ?? 0} marked.
                {' '}<button type="button" onClick={() => onNav('pipeline')} className="font-medium text-accent hover:opacity-70">Open the Pipeline view →</button></p>
            )}
          </section>
        )}
      </form>
    </div>
  )
}
