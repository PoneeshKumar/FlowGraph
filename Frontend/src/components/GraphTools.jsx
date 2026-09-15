import { useState } from 'react'
import { DemoNote, ErrorNote } from './ui'
import { NotInSnapshotError } from '../services/dataSource'
import { fmtAmount, shortId } from '../lib/format'

const WINDOWS = ['24h', '7d', '30d']

/** Shortest path + flow between two accounts. Calls back with path elements to draw. */
export default function GraphTools({ source, defaultA = '', defaultB = '', onPath }) {
  const [a, setA] = useState(defaultA)
  const [b, setB] = useState(defaultB)
  const [window, setWindow] = useState('7d')
  const [path, setPath] = useState(null)
  const [flow, setFlow] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    if (!a.trim() || !b.trim()) return
    setBusy(true); setError(null); setPath(null); setFlow(null)
    try {
      const [p, f] = await Promise.all([source.graph.shortestPath(a.trim(), b.trim()), source.graph.flow(a.trim(), b.trim(), window)])
      setPath(p); setFlow(f); onPath?.(p)
    } catch (e) { setError(e) } finally { setBusy(false) }
  }

  const field = 'w-full border-b border-line bg-transparent py-1 font-mono text-[11px] text-ink outline-none placeholder:text-ink-4 focus:border-line-2'
  return (
    <div className="space-y-3">
      <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">Path & flow between two accounts</p>
      <input value={a} onChange={e => setA(e.target.value)} placeholder="from account id" className={field} />
      <input value={b} onChange={e => setB(e.target.value)} placeholder="to account id" className={field} />
      <div className="flex items-center gap-3">
        {WINDOWS.map(w => (
          <button key={w} type="button" onClick={() => setWindow(w)} className={`text-[11px] ${window === w ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}>{w}</button>
        ))}
        <button type="button" onClick={run} disabled={busy} className="ml-auto text-[12px] font-medium text-accent hover:opacity-70 disabled:opacity-40">{busy ? 'Searching…' : 'Find'}</button>
      </div>
      {error instanceof NotInSnapshotError ? <DemoNote>{error.message}</DemoNote> : error ? <ErrorNote error={error} /> : null}
      {path && (
        <p className="text-[12px] text-ink-2">
          {path.nodes.length ? <>Path of <span className="font-mono">{path.nodes.length - 1}</span> hop{path.nodes.length === 2 ? '' : 's'}: {path.nodes.map(n => shortId(n.data.id)).join(' → ')}</> : 'No directed path within 10 hops.'}
        </p>
      )}
      {flow && (
        <p className="font-mono text-[11px] text-ink-3 tnum">
          {flow.tx_count} direct transfer{flow.tx_count === 1 ? '' : 's'} in {flow.window} · {fmtAmount(flow.total_volume_cents)} total
        </p>
      )}
    </div>
  )
}
