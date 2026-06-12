import { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { GRAPH_NODES, GRAPH_EDGES } from '../data/mockData'
import { RISK_VAR } from './ui'

function getNodeRadius(node) {
  if (node.id === 'hub') return 20
  return { critical: 16, high: 13, medium: 11, low: 10 }[node.risk]
}

function getEdgeWidth(weight) {
  if (weight > 1000000) return 2.2
  if (weight > 500000)  return 1.8
  if (weight > 200000)  return 1.3
  return 1.0
}

function ArrowMarkers() {
  return (
    <defs>
      {Object.entries(RISK_VAR).map(([risk, color]) => (
        <marker key={risk} id={`arrow-${risk}`} markerWidth="5" markerHeight="5" refX="4.5" refY="2.5" orient="auto">
          <path d="M0,0.5 L0,4.5 L4.5,2.5 z" fill={color} opacity="0.65" />
        </marker>
      ))}
      <marker id="arrow-cycle" markerWidth="5" markerHeight="5" refX="4.5" refY="2.5" orient="auto">
        <path d="M0,0.5 L0,4.5 L4.5,2.5 z" fill="var(--critical)" />
      </marker>
      {/* Soft halo for nodes */}
      <filter id="node-glow" x="-80%" y="-80%" width="260%" height="260%">
        <feGaussianBlur stdDeviation="3.5" result="blur" />
        <feMerge>
          <feMergeNode in="blur" />
          <feMergeNode in="SourceGraphic" />
        </feMerge>
      </filter>
    </defs>
  )
}

function GraphEdge({ edge, fromNode, toNode, isActive }) {
  const dx = toNode.x - fromNode.x
  const dy = toNode.y - fromNode.y
  const len = Math.sqrt(dx * dx + dy * dy)
  const frac = (len - getNodeRadius(toNode) - 2) / len
  const x2 = fromNode.x + dx * frac
  const y2 = fromNode.y + dy * frac

  const width = getEdgeWidth(edge.weight)
  const dur = 0.7 + (1 - Math.min(edge.weight / 2000000, 1)) * 0.6

  return (
    <g>
      <line
        x1={fromNode.x} y1={fromNode.y} x2={x2} y2={y2}
        strokeWidth={edge.isCycle ? width + 0.4 : width}
        opacity={edge.isCycle ? 0.75 : 0.4}
        strokeDasharray={edge.isCycle ? '5 5' : 'none'}
        markerEnd={`url(#arrow-${edge.isCycle ? 'cycle' : fromNode.risk})`}
        stroke={edge.isCycle ? 'var(--critical)' : 'var(--line-strong)'}
      />
      {isActive && (
        <line
          x1={fromNode.x} y1={fromNode.y} x2={x2} y2={y2}
          strokeWidth={width + 0.6}
          opacity={edge.isCycle ? 0.9 : 0.6}
          strokeDasharray={edge.isCycle ? '8 10' : '5 12'}
          strokeLinecap="round"
          stroke={edge.isCycle ? 'var(--critical)' : 'var(--accent)'}
          style={{ animation: `flowDash ${dur}s linear infinite` }}
        />
      )}
    </g>
  )
}

function GraphNode({ node, isSelected, onSelect, index }) {
  const r = getNodeRadius(node)
  const color = RISK_VAR[node.risk]

  return (
    <motion.g
      transform={`translate(${node.x},${node.y})`}
      onClick={() => onSelect(node)}
      className="cursor-pointer"
      initial={{ opacity: 0, scale: 0.5 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ delay: 0.15 + index * 0.045, type: 'spring', stiffness: 260, damping: 20 }}
    >
      {isSelected && (
        <motion.circle
          r={r + 6}
          fill="none"
          stroke={color}
          strokeWidth="1.5"
          initial={{ opacity: 0, scale: 0.8 }}
          animate={{ opacity: 0.45, scale: 1 }}
        />
      )}
      {/* glassy body */}
      <circle
        r={r}
        fill={color}
        fillOpacity={isSelected ? 0.22 : 0.1}
        stroke={color}
        strokeWidth={isSelected ? 2 : 1.4}
        filter={node.risk === 'critical' ? 'url(#node-glow)' : undefined}
        style={{ transition: 'fill-opacity .2s ease' }}
      />
      <circle r={r * 0.3} fill={color} opacity={0.85} />
      <text
        y={r + 13}
        textAnchor="middle"
        fontSize={isSelected ? 9.5 : 9}
        fontWeight={isSelected ? 700 : 500}
        style={{
          fontFamily: 'var(--font-mono)',
          fill: isSelected ? color : 'var(--ink-3)',
          userSelect: 'none',
          transition: 'fill .15s',
        }}
      >
        {node.label}
      </text>
    </motion.g>
  )
}

function StatTile({ label, value, color }) {
  return (
    <div className="glass-soft rounded-lg px-2.5 py-2">
      <div className="mb-1 text-[9px] font-bold uppercase tracking-[0.1em] text-ink-4">{label}</div>
      <div className="font-mono text-[13px] font-semibold tnum" style={{ color }}>{value}</div>
    </div>
  )
}

function NodePanel({ node, onClose }) {
  const color = RISK_VAR[node.risk]
  const edges = GRAPH_EDGES.filter(e => e.from === node.id || e.to === node.id)
  const inbound = edges.filter(e => e.to === node.id)
  const outbound = edges.filter(e => e.from === node.id)
  const getLabel = id => GRAPH_NODES.find(n => n.id === id)?.label || id

  return (
    <motion.div
      initial={{ opacity: 0, x: 32, filter: 'blur(4px)' }}
      animate={{ opacity: 1, x: 0, filter: 'blur(0px)' }}
      exit={{ opacity: 0, x: 32, filter: 'blur(4px)' }}
      transition={{ duration: 0.32, ease: [0.32, 0.72, 0, 1] }}
      className="glass-strong absolute bottom-3 right-3 top-3 flex w-[264px] flex-col overflow-y-auto rounded-xl"
      style={{ borderTop: `2px solid ${color}` }}
    >
      <div className="flex items-start justify-between border-b border-line px-4 py-3.5">
        <div>
          <div className="font-mono text-sm font-bold text-ink">{node.label}</div>
          <div className="mt-0.5 text-[11px] capitalize text-ink-3">{node.type}</div>
        </div>
        <div className="flex items-center gap-2">
          <span
            className="rounded-md border px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.06em]"
            style={{ color, borderColor: `color-mix(in oklab, ${color} 35%, transparent)`, background: `color-mix(in oklab, ${color} 12%, transparent)` }}
          >
            {node.risk}
          </span>
          <button
            onClick={onClose}
            className="flex h-[22px] w-[22px] items-center justify-center rounded-full border border-line bg-hover text-sm leading-none text-ink-3 transition-colors hover:text-ink"
          >
            ×
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 border-b border-line px-4 py-3">
        <StatTile label="Total Volume" value={`$${(node.volume / 1000000).toFixed(2)}M`} color={color} />
        <StatTile label="Connections"  value={edges.length} color={color} />
        <StatTile label="Inbound"      value={inbound.length} color={color} />
        <StatTile label="Outbound"     value={outbound.length} color={color} />
      </div>

      <div className="flex-1 px-4 py-3">
        <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.12em] text-ink-3">Connections</div>
        {edges.map(edge => {
          const isOut = edge.from === node.id
          return (
            <div key={edge.id} className="flex items-center justify-between border-b border-line py-1.5 text-[11px] last:border-b-0">
              <div className="flex items-center gap-1.5">
                {edge.isCycle && <span className="text-[9px] font-bold text-critical">⟲</span>}
                <span className={`text-[9px] font-bold tracking-[0.05em] ${isOut ? 'text-high' : 'text-accent'}`}>
                  {isOut ? 'OUT' : 'IN'}
                </span>
                <span className="font-mono text-[10.5px] text-ink-2">{getLabel(isOut ? edge.to : edge.from)}</span>
              </div>
              <span className="font-mono text-[10px] text-ink-3 tnum">${(edge.weight / 1000).toFixed(0)}K</span>
            </div>
          )
        })}
      </div>

      {node.risk === 'critical' && (
        <div className="mx-4 mb-4 rounded-lg border border-critical/30 bg-critical/10 px-3 py-2.5" style={{ borderLeft: '2px solid var(--critical)' }}>
          <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.08em] text-critical">AI Risk Note</div>
          <div className="text-[11.5px] leading-relaxed text-ink-2">
            Central hub with disproportionate network influence. Recommend immediate review.
          </div>
        </div>
      )}
    </motion.div>
  )
}

export default function GraphExplorer() {
  const [selectedNode, setSelectedNode] = useState(null)
  const [activeEdges, setActiveEdges] = useState(new Set(['e1', 'e3', 'e4', 'e7', 'e8', 'e10', 'e11', 'e14']))
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [isPanning, setIsPanning] = useState(false)
  const panStart = useRef(null)
  const svgRef = useRef(null)

  useEffect(() => {
    const timeoutIds = new Set()
    const iv = setInterval(() => {
      const ids = GRAPH_EDGES.map(e => e.id)
      const id = ids[Math.floor(Math.random() * ids.length)]
      setActiveEdges(prev => new Set(prev).add(id))
      const timeoutId = setTimeout(() => {
        setActiveEdges(s => { const n = new Set(s); n.delete(id); return n })
        timeoutIds.delete(timeoutId)
      }, 2200)
      timeoutIds.add(timeoutId)
    }, 700)
    return () => { clearInterval(iv); timeoutIds.forEach(clearTimeout) }
  }, [])

  const handleWheel = useCallback(e => {
    e.preventDefault()
    setZoom(z => Math.max(0.5, Math.min(2.5, z - e.deltaY * 0.001)))
  }, [])

  const handleMouseDown = useCallback(e => {
    if (e.target === svgRef.current || e.target.tagName === 'svg') {
      setIsPanning(true)
      panStart.current = { x: e.clientX - pan.x, y: e.clientY - pan.y }
    }
  }, [pan])

  const handleMouseMove = useCallback(e => {
    if (!isPanning || !panStart.current) return
    setPan({ x: e.clientX - panStart.current.x, y: e.clientY - panStart.current.y })
  }, [isPanning])

  const handleMouseUp = useCallback(() => setIsPanning(false), [])

  const nodeMap = Object.fromEntries(GRAPH_NODES.map(n => [n.id, n]))
  const riskCounts = GRAPH_NODES.reduce((acc, n) => { acc[n.risk] = (acc[n.risk] || 0) + 1; return acc }, {})

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <header className="glass z-10 mx-4 mt-4 flex shrink-0 items-center justify-between gap-4 rounded-xl px-6 py-3.5">
        <div>
          <h1 className="font-display text-[19px] font-medium tracking-tight text-ink">Graph Explorer</h1>
          <div className="mt-0.5 text-xs text-ink-3 tnum">{GRAPH_NODES.length} nodes · {GRAPH_EDGES.length} edges</div>
        </div>
        <div className="flex items-center gap-4">
          {Object.entries(RISK_VAR).map(([risk, color]) => (
            <div key={risk} className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ background: color }} />
              <span className="text-[11px] capitalize text-ink-2">
                {risk} <span className="font-mono text-[10px] text-ink-4 tnum">({riskCounts[risk] || 0})</span>
              </span>
            </div>
          ))}
          <span className="h-3.5 w-px bg-line-2" />
          {[['−', -0.2], ['+', 0.2]].map(([lbl, d]) => (
            <button
              key={lbl}
              onClick={() => setZoom(z => Math.max(0.5, Math.min(2.5, z + d)))}
              className="glass-soft flex h-[26px] w-[26px] items-center justify-center rounded-lg text-[15px] text-ink-2 transition-colors duration-150 hover:text-ink active:scale-95"
            >
              {lbl}
            </button>
          ))}
          <span className="glass-soft rounded-md px-2 py-1 font-mono text-[11px] text-ink-3 tnum">
            {Math.round(zoom * 100)}%
          </span>
        </div>
      </header>

      {/* Canvas */}
      <div
        className={`relative m-4 flex-1 overflow-hidden rounded-xl border border-line ${isPanning ? 'cursor-grabbing' : 'cursor-grab'}`}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        {/* Fine dot lattice */}
        <div
          className="pointer-events-none absolute inset-0 opacity-50"
          style={{
            backgroundImage: 'radial-gradient(circle, var(--line-strong) 1px, transparent 1px)',
            backgroundSize: '28px 28px',
          }}
        />

        <svg
          ref={svgRef}
          width="100%" height="100%"
          viewBox="0 0 560 500"
          style={{
            transform: `scale(${zoom}) translate(${pan.x / zoom}px, ${pan.y / zoom}px)`,
            transformOrigin: 'center',
            transition: isPanning ? 'none' : 'transform 0.18s var(--ease-fluid)',
            overflow: 'visible',
          }}
        >
          <ArrowMarkers />
          <g>
            {GRAPH_EDGES.map(edge => {
              const from = nodeMap[edge.from]
              const to = nodeMap[edge.to]
              if (!from || !to) return null
              return <GraphEdge key={edge.id} edge={edge} fromNode={from} toNode={to} isActive={activeEdges.has(edge.id)} />
            })}
          </g>
          <g>
            {GRAPH_NODES.map((node, i) => (
              <GraphNode
                key={node.id}
                node={node}
                index={i}
                isSelected={selectedNode?.id === node.id}
                onSelect={n => setSelectedNode(prev => (prev?.id === n.id ? null : n))}
              />
            ))}
          </g>
        </svg>

        {/* Live metrics */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.25, duration: 0.4, ease: [0.32, 0.72, 0, 1] }}
          className="glass absolute left-3 top-3 min-w-[150px] rounded-xl px-3.5 py-3"
        >
          <div className="mb-2.5 text-[10px] font-bold uppercase tracking-[0.12em] text-ink-3">Live Metrics</div>
          {[
            ['Active edges', `${activeEdges.size} / ${GRAPH_EDGES.length}`],
            ['Total flow',   '$8.42M'],
            ['Avg hop time', '4.2 min'],
          ].map(([k, v]) => (
            <div key={k} className="mb-1.5 flex justify-between gap-4 last:mb-0">
              <span className="text-[11px] text-ink-2">{k}</span>
              <span className="font-mono text-[11px] font-semibold text-ink tnum">{v}</span>
            </div>
          ))}
        </motion.div>

        {/* Cycle alert */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.45, duration: 0.4, ease: [0.32, 0.72, 0, 1] }}
          className="glass absolute bottom-3 left-3 flex items-center gap-2 rounded-lg px-3 py-2"
          style={{ borderLeft: '2px solid var(--critical)' }}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-critical [animation:pulseSoft_1.5s_ease-in-out_infinite]" />
          <span className="text-[11px] font-medium text-critical">
            Cycle: ACC-4471 → EXC-0044 → ACC-6612 → ACC-4471
          </span>
        </motion.div>

        <AnimatePresence>
          {selectedNode && (
            <NodePanel key={selectedNode.id} node={selectedNode} onClose={() => setSelectedNode(null)} />
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
