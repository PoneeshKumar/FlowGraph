import { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { GRAPH_NODES, GRAPH_EDGES } from '../data/mockData'
import { RISK_VAR, PageHeader } from './ui'

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

function GraphEdge({ edge, fromNode, toNode, isActive, index }) {
  const dx = toNode.x - fromNode.x
  const dy = toNode.y - fromNode.y
  const len = Math.sqrt(dx * dx + dy * dy)
  const frac = (len - getNodeRadius(toNode) - 2) / len
  const x2 = fromNode.x + dx * frac
  const y2 = fromNode.y + dy * frac

  const width = getEdgeWidth(edge.weight)
  const dashPattern = edge.isCycle ? '6 14' : '4 14'
  const dashPeriod = edge.isCycle ? 20 : 18
  const dur = 1.4 + (1 - Math.min(edge.weight / 2000000, 1)) * 1.0
  const flowOpacity = isActive ? (edge.isCycle ? 0.82 : 0.5) : 0

  return (
    <g>
      <motion.line
        x1={fromNode.x} y1={fromNode.y} x2={x2} y2={y2}
        strokeWidth={edge.isCycle ? width + 0.4 : width}
        strokeDasharray={edge.isCycle ? '5 5' : 'none'}
        markerEnd={`url(#arrow-${edge.isCycle ? 'cycle' : fromNode.risk})`}
        stroke={edge.isCycle ? 'var(--critical)' : 'var(--line-strong)'}
        vectorEffect="non-scaling-stroke"
        initial={{ opacity: 0 }}
        animate={{ opacity: edge.isCycle ? 0.75 : 0.4 }}
        transition={{ delay: 0.25 + index * 0.025, duration: 0.6, ease: 'easeOut' }}
      />
      {/* Flow pulse — always mounted; opacity + dash loop avoid mount/unmount pops */}
      <line
        x1={fromNode.x}
        y1={fromNode.y}
        x2={x2}
        y2={y2}
        strokeWidth={width + 0.5}
        strokeDasharray={dashPattern}
        strokeLinecap="round"
        stroke={edge.isCycle ? 'var(--critical)' : 'var(--accent)'}
        vectorEffect="non-scaling-stroke"
        style={{
          opacity: flowOpacity,
          transition: 'opacity 0.9s ease-in-out',
          animation: `flowDash ${dur}s linear infinite`,
          animationDelay: `${-(index * 0.22) % dur}s`,
          ['--dash-period']: `-${dashPeriod}`,
        }}
      />
    </g>
  )
}

function GraphNode({ node, isSelected, onSelect, index }) {
  const r = getNodeRadius(node)
  const color = RISK_VAR[node.risk]
  const delay = 80 + index * 40

  return (
    <g
      transform={`translate(${node.x},${node.y})`}
      onClick={() => onSelect(node)}
      style={{ cursor: 'pointer' }}
    >
      <motion.g
        initial={{ opacity: 0, scale: 0.5 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ delay: 0.1 + index * 0.04, type: 'spring', stiffness: 260, damping: 20 }}
      >
        {isSelected && (
          <motion.circle
            r={r + 5}
            fill="none"
            stroke={color}
            strokeWidth="1.5"
            initial={{ opacity: 0 }}
            animate={{ opacity: 0.4 }}
          />
        )}

        <circle
          r={r}
          stroke={color}
          strokeWidth={isSelected ? 2 : 1.5}
          style={{
            fill: isSelected ? `var(--risk-${node.risk}-bg)` : 'var(--bg-card)',
            transition: 'all 0.18s ease',
            filter: 'drop-shadow(0 1px 3px rgba(0,0,0,0.10))',
            animation: `nodePop 0.35s ease ${delay}ms both`,
          }}
        />

        <circle r={r * 0.32} fill={color} opacity={0.75} />

        {node.pagerank !== undefined && (
          <g>
            <rect x={r + 4} y={-8} width="54" height="16" rx="8" fill="var(--bg-card)" stroke={color} strokeWidth="1" />
            <text x={r + 31} y={3} textAnchor="middle" fontSize="7" fontFamily="Space Mono, monospace" fontWeight="700" fill={color}>
              PR {node.pagerank.toFixed(3)}
            </text>
          </g>
        )}

        <text
          y={r + 12}
          textAnchor="middle"
          fontSize={isSelected ? 9.5 : 9}
          fontFamily="Space Mono, monospace"
          fontWeight={isSelected ? 700 : 400}
          style={{ fill: isSelected ? color : 'var(--text-muted)', userSelect: 'none', transition: 'all 0.15s' }}
        >
          {node.label}
        </text>
      </motion.g>
    </g>
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
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 24 }}
      transition={{ duration: 0.28, ease: [0.32, 0.72, 0, 1] }}
      className="glass absolute bottom-4 right-4 top-4 flex w-[300px] flex-col overflow-y-auto rounded-xl"
    >
      <div className="flex items-start justify-between px-5 pb-3.5 pt-4" style={{ boxShadow: `inset 0 2px 0 0 ${color}` }}>
        <div>
          <div className="font-mono text-[15px] font-bold text-ink">{node.label}</div>
          <div className="mt-0.5 text-[11px] capitalize text-ink-3">
            {node.type} · <span style={{ color }} className="font-semibold uppercase tracking-[0.05em]">{node.risk}</span>
          </div>
        </div>
        <button
          onClick={onClose}
          className="flex h-[22px] w-[22px] items-center justify-center rounded-full text-base leading-none text-ink-3 transition-colors hover:bg-hover hover:text-ink"
        >
          ×
        </button>
      </div>

      {/* Stat strip — divided by hairlines, not tiles */}
      <div className="grid grid-cols-4">
        {[
          ['Volume',   `$${(node.volume / 1000000).toFixed(1)}M`],
          ['Links',    edges.length],
          ['In',       inbound.length],
          ['Out',      outbound.length],
        ].map(([label, value]) => (
          <div key={label} className="px-3 py-2.5">
            <div className="mb-0.5 text-[9px] font-bold uppercase tracking-[0.1em] text-ink-4">{label}</div>
            <div className="font-mono text-[13px] font-semibold text-ink tnum">{value}</div>
          </div>
        ))}
      </div>

      <div className="flex-1 px-5 py-3.5">
        <div className="mb-1.5 text-[10px] font-bold uppercase tracking-[0.12em] text-ink-3">Connections</div>
        {edges.map(edge => {
          const isOut = edge.from === node.id
          return (
            <div key={edge.id} className="flex items-center justify-between py-2 text-[11.5px]">
              <div className="flex items-center gap-2">
                {edge.isCycle && <span className="text-[10px] font-bold text-critical">⟲</span>}
                <span className={`w-7 text-[9px] font-bold tracking-[0.05em] ${isOut ? 'text-high' : 'text-accent'}`}>
                  {isOut ? 'OUT' : 'IN'}
                </span>
                <span className="font-mono text-[11px] text-ink-2">{getLabel(isOut ? edge.to : edge.from)}</span>
              </div>
              <span className="font-mono text-[11px] text-ink-3 tnum">${(edge.weight / 1000).toFixed(0)}K</span>
            </div>
          )
        })}
      </div>

      {node.risk === 'critical' && (
        <div className="px-5 py-3.5">
          <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.08em] text-critical">AI Risk Note</div>
          <div className="text-[12px] leading-relaxed text-ink-2">
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
      }, 3200)
      timeoutIds.add(timeoutId)
    }, 2000)
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
      <PageHeader
        title="Graph Explorer"
        subtitle={<span className="tnum">{GRAPH_NODES.length} nodes · {GRAPH_EDGES.length} edges</span>}
      >
        {Object.entries(RISK_VAR).map(([risk, color]) => (
          <div key={risk} className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: color }} />
            <span className="text-[11px] capitalize text-ink-2">
              {risk} <span className="font-mono text-[10px] text-ink-4 tnum">({riskCounts[risk] || 0})</span>
            </span>
          </div>
        ))}
        <span className="mx-1 h-3.5 w-px bg-line-2" />
        {[['−', -0.2], ['+', 0.2]].map(([lbl, d]) => (
          <button
            key={lbl}
            onClick={() => setZoom(z => Math.max(0.5, Math.min(2.5, z + d)))}
            className="flex h-[26px] w-[26px] items-center justify-center rounded-lg text-[15px] text-ink-2 transition-colors duration-150 hover:bg-hover hover:text-ink active:scale-95"
          >
            {lbl}
          </button>
        ))}
        <span className="w-[42px] text-right font-mono text-[11px] text-ink-3 tnum">
          {Math.round(zoom * 100)}%
        </span>
      </PageHeader>

      {/* Canvas — full bleed, no frame */}
      <div
        className={`relative flex-1 overflow-hidden ${isPanning ? 'cursor-grabbing' : 'cursor-grab'}`}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        {/* Fine dot lattice */}
        <div
          className="pointer-events-none absolute inset-0 opacity-40"
          style={{
            backgroundImage: 'radial-gradient(circle, var(--line-strong) 1px, transparent 1px)',
            backgroundSize: '28px 28px',
          }}
        />

        <svg
          ref={svgRef}
          width="100%" height="100%"
          viewBox="0 0 560 500"
          preserveAspectRatio="xMidYMid meet"
          style={{
            transform: `scale(${zoom}) translate(${pan.x / zoom}px, ${pan.y / zoom}px)`,
            transformOrigin: 'center',
            transition: isPanning ? 'none' : 'transform 0.18s var(--ease-fluid)',
            overflow: 'visible',
          }}
        >
          <ArrowMarkers />
          <g>
            {GRAPH_EDGES.map((edge, i) => {
              const from = nodeMap[edge.from]
              const to = nodeMap[edge.to]
              if (!from || !to) return null
              return <GraphEdge key={edge.id} edge={edge} fromNode={from} toNode={to} index={i} isActive={activeEdges.has(edge.id)} />
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

        {/* Live metrics — floating glass, earns its panel */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2, duration: 0.4, ease: [0.32, 0.72, 0, 1] }}
          className="glass absolute left-4 top-4 min-w-[150px] rounded-lg px-3.5 py-3"
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
          transition={{ delay: 0.4, duration: 0.4, ease: [0.32, 0.72, 0, 1] }}
          className="glass absolute bottom-4 left-4 flex items-center gap-2 rounded-lg px-3.5 py-2.5"
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
