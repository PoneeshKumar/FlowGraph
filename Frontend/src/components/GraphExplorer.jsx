import { useMemo, useState } from 'react'
import { GraphCanvas } from './GraphCanvas'
import { InspectorSidebar } from './InspectorSidebar'
import GraphTools from './GraphTools'
import { Skeleton, ErrorNote, DemoNote, EmptyNote } from './ui'
import { useDataSource } from '../services/DataSourceProvider'
import { useAsync } from '../hooks/useAsync'
import { NotInSnapshotError } from '../services/dataSource'
import { shortId } from '../lib/format'

const LENSES = [['risk', 'Risk tier'], ['gnn', 'GNN heat'], ['community', 'Community'], ['pagerank', 'PageRank']]

export default function GraphExplorer({ onNav, params }) {
  const { source, mode } = useDataSource()
  const target = params?.account || ''
  const depth = Number(params?.depth || 2)
  const [query, setQuery] = useState(target)
  const [lens, setLens] = useState('risk')
  const [selected, setSelected] = useState(null)
  const [pathIds, setPathIds] = useState(null)

  const featured = useAsync(() => source.featured(), [source.mode])
  const graph = useAsync(() => (target ? source.graph.subgraph(target, depth) : Promise.resolve(null)), [source.mode, target, depth])
  const elements = useMemo(() => graph.data || { nodes: [], edges: [] }, [graph.data])

  const go = (id, d = depth) => { setSelected(null); setPathIds(null); onNav('graph', { account: id, depth: d }) }
  const onPath = (p) => setPathIds(p.nodes.map(n => n.data.id))

  return (
    <div className="flex h-full min-w-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 flex-wrap items-center gap-4 px-8 pb-3 pt-1">
          <form onSubmit={e => { e.preventDefault(); if (query.trim()) go(query.trim()) }}
            className="flex items-center gap-2 border-b border-line py-1 focus-within:border-line-2">
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Account id…"
              className="w-72 bg-transparent font-mono text-xs text-ink outline-none placeholder:text-ink-4" />
            <select value={depth} onChange={e => target && go(target, Number(e.target.value))} className="bg-transparent text-[11px] text-ink-3 outline-none">
              {[1, 2, 3].map(d => <option key={d} value={d}>{d}-hop</option>)}
            </select>
            <button type="submit" className="text-[12px] font-medium text-accent hover:opacity-70">Explore</button>
          </form>
          <div className="flex items-center gap-4">
            {LENSES.map(([id, label]) => (
              <button key={id} type="button" onClick={() => setLens(id)} className={`text-[12px] ${lens === id ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2'}`}>{label}</button>
            ))}
          </div>
          {graph.data && <span className="ml-auto font-mono text-[11px] text-ink-4 tnum">{elements.nodes.length} accounts · {elements.edges.length} flows</span>}
        </div>

        {featured.data?.length > 0 && (
          <div className="flex shrink-0 flex-wrap items-center gap-2 px-8 pb-3">
            <span className="text-[11px] text-ink-4">{mode === 'demo' ? 'Featured accounts in this snapshot:' : 'Try:'}</span>
            {featured.data.slice(0, 12).map(id => (
              <button key={id} type="button" onClick={() => { setQuery(id); go(id) }}
                className={`rounded-full px-2 py-0.5 font-mono text-[10.5px] ${id === target ? 'bg-accent/10 text-accent' : 'bg-hover text-ink-2 hover:text-ink'}`}>{shortId(id)}</button>
            ))}
          </div>
        )}

        <div className="relative min-h-0 flex-1 border-t border-line">
          {graph.error instanceof NotInSnapshotError ? <div className="p-8"><DemoNote>{graph.error.message}</DemoNote></div>
            : graph.error ? <div className="p-8"><ErrorNote error={graph.error} onRetry={graph.reload} /></div>
            : graph.loading && target ? <div className="p-8"><Skeleton className="h-full min-h-[300px] w-full" /></div>
            : !target ? <EmptyNote>Search an account id, or pick a featured account, to draw its neighbourhood.</EmptyNote>
            : elements.nodes.length === 0 ? <EmptyNote>No flows found around {shortId(target)}.</EmptyNote>
            : <GraphCanvas elements={elements} lens={lens} selectedId={selected?.id} onSelectNode={setSelected} highlightIds={pathIds} />}
          <div className="glass absolute bottom-4 left-4 w-[300px] rounded-lg p-4">
            <GraphTools key={target} source={source} defaultA={target} onPath={onPath} />
          </div>
        </div>
      </div>

      <InspectorSidebar source={source} node={selected} onClose={() => setSelected(null)} onExplore={(id) => { setQuery(id); go(id) }} />
    </div>
  )
}
