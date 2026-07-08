import { useState, useEffect, useRef, useCallback } from 'react'
import { GRAPH_NODES, GRAPH_EDGES } from '../data/mockData'

const RISK_COLORS = {
  critical: '#C8241A',
  high:     '#B45309',
  medium:   '#92620A',
  low:      '#0C7A5A',
}

const RISK_BG = {
  critical: '#FEF0EF',
  high:     '#FEF3E7',
  medium:   '#FEFAE7',
  low:      '#EAF5F1',
}

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
      {Object.entries(RISK_COLORS).map(([risk, color]) => (
        <marker key={risk} id={`arrow-${risk}`} markerWidth="5" markerHeight="5" refX="4.5" refY="2.5" orient="auto">
          <path d="M0,0.5 L0,4.5 L4.5,2.5 z" fill={color} opacity="0.6"/>
        </marker>
      ))}
      <marker id="arrow-cycle" markerWidth="5" markerHeight="5" refX="4.5" refY="2.5" orient="auto">
        <path d="M0,0.5 L0,4.5 L4.5,2.5 z" fill="#C8241A"/>
      </marker>
    </defs>
  )
}

function GraphEdge({ edge, fromNode, toNode, isActive }) {
  const dx = toNode.x - fromNode.x
  const dy = toNode.y - fromNode.y
  const len = Math.sqrt(dx * dx + dy * dy)
  const toR = getNodeRadius(toNode) + 2
  const frac = (len - toR) / len

  const x2 = fromNode.x + dx * frac
  const y2 = fromNode.y + dy * frac

  const color = edge.isCycle ? '#C8241A' : RISK_COLORS[fromNode.risk]
  const width = getEdgeWidth(edge.weight)
  const dur = 0.7 + (1 - Math.min(edge.weight / 2000000, 1)) * 0.6

  return (
    <g>
      {/* Static base line */}
      <line
        x1={fromNode.x} y1={fromNode.y}
        x2={x2} y2={y2}
        strokeWidth={edge.isCycle ? width + 0.4 : width}
        opacity={edge.isCycle ? 0.7 : 0.5}
        strokeDasharray={edge.isCycle ? '5 5' : 'none'}
        markerEnd={`url(#arrow-${edge.isCycle ? 'cycle' : fromNode.risk})`}
        style={{ stroke: edge.isCycle ? '#C8241A' : 'var(--border-strong)' }}
      />
      {/* Animated flow overlay — only when active */}
      {isActive && (
        <line
          x1={fromNode.x} y1={fromNode.y}
          x2={x2} y2={y2}
          strokeWidth={width + 0.6}
          opacity={edge.isCycle ? 0.85 : 0.55}
          strokeDasharray={edge.isCycle ? '8 10' : '5 12'}
          strokeLinecap="round"
          markerEnd={`url(#arrow-${edge.isCycle ? 'cycle' : 'low'})`}
          style={{ stroke: edge.isCycle ? '#C8241A' : 'var(--accent)', animation: `flowDash ${dur}s linear infinite` }}
        />
      )}
    </g>
  )
}

function GraphNode({ node, isSelected, onSelect, delay }) {
  const r = getNodeRadius(node)
  const color = RISK_COLORS[node.risk]
  const bg = RISK_BG[node.risk]

  return (
    <g
      transform={`translate(${node.x},${node.y})`}
      onClick={() => onSelect(node)}
      style={{ cursor: 'pointer' }}
    >
      {/* Selection ring */}
      {isSelected && (
        <circle r={r + 5} fill="none" stroke={color} strokeWidth="1.5" opacity="0.4"/>
      )}

      {/* Node body — themed fill, colored border */}
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

      {/* Center dot */}
      <circle r={r * 0.32} fill={color} opacity={0.75}/>

      {node.pagerank !== undefined && (
        <g>
          <rect x={r + 4} y={-8} width="54" height="16" rx="8" fill="var(--bg-card)" stroke={color} strokeWidth="1" />
          <text x={r + 31} y={3} textAnchor="middle" fontSize="7" fontFamily="Space Mono, monospace" fontWeight="700" fill={color}>
            PR {node.pagerank.toFixed(3)}
          </text>
        </g>
      )}

      {/* Label */}
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
    </g>
  )
}

function NodePanel({ node, onClose }) {
  if (!node) return null
  const color = RISK_COLORS[node.risk]
  const edges = GRAPH_EDGES.filter(e => e.from === node.id || e.to === node.id)
  const inbound  = edges.filter(e => e.to === node.id)
  const outbound = edges.filter(e => e.from === node.id)
  const getLabel = id => GRAPH_NODES.find(n => n.id === id)?.label || id

  return (
    <div style={{
      position: 'absolute', right: 14, top: 14, bottom: 14,
      width: 256,
      background: 'var(--bg-card)',
      border: '1px solid var(--border)',
      borderTop: `3px solid ${color}`,
      borderRadius: 'var(--radius)',
      boxShadow: 'var(--shadow-lg)',
      overflow: 'auto',
      animation: 'fadeSlideIn 0.18s ease',
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Header */}
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
            {node.label}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2, textTransform: 'capitalize' }}>
            {node.type}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em',
            color, background: `var(--risk-${node.risk}-bg)`,
            border: `1px solid ${color}40`,
            padding: '3px 8px', borderRadius: 4,
          }}>
            {node.risk}
          </span>
          <button onClick={onClose} style={{
            width: 22, height: 22, borderRadius: '50%',
            background: 'var(--bg-subtle)', border: '1px solid var(--border)',
            color: 'var(--text-muted)', display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 14, lineHeight: 1,
          }}>×</button>
        </div>
      </div>

      {/* Stats */}
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {[
            { label: 'Total Volume',  value: `$${(node.volume / 1000000).toFixed(2)}M` },
            { label: 'Connections',   value: edges.length },
            { label: 'Inbound',       value: inbound.length },
            { label: 'Outbound',      value: outbound.length },
          ].map(s => (
            <div key={s.label} style={{
              background: 'var(--bg-subtle)', border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)', padding: '8px 10px',
            }}>
              <div style={{ fontSize: 9, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>
                {s.label}
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, color }}>
                {s.value}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Connections */}
      <div style={{ padding: '12px 16px', flex: 1 }}>
        <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', marginBottom: 8 }}>
          Connections
        </div>
        {edges.map(edge => {
          const isOut = edge.from === node.id
          const other = isOut ? getLabel(edge.to) : getLabel(edge.from)
          return (
            <div key={edge.id} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '6px 0', borderBottom: '1px solid var(--border)', fontSize: 11,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {edge.isCycle && (
                  <span style={{ fontSize: 9, color: 'var(--risk-critical)', fontWeight: 700 }}>⟲</span>
                )}
                <span style={{
                  fontSize: 9, fontWeight: 700, letterSpacing: '0.05em',
                  color: isOut ? 'var(--risk-high)' : 'var(--accent)',
                }}>
                  {isOut ? 'OUT' : 'IN '}
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', fontSize: 10.5 }}>
                  {other}
                </span>
              </div>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)' }}>
                ${(edge.weight / 1000).toFixed(0)}K
              </span>
            </div>
          )
        })}
      </div>

      {/* AI note for critical */}
      {node.risk === 'critical' && (
        <div style={{
          margin: '0 16px 16px',
          padding: '10px 12px',
          background: 'var(--risk-critical-bg)',
          border: '1px solid var(--risk-critical-border)',
          borderLeft: '3px solid var(--risk-critical)',
          borderRadius: 'var(--radius-sm)',
        }}>
          <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--risk-critical)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 5 }}>
            AI Risk Note
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            Central hub with disproportionate network influence. Recommend immediate review.
          </div>
        </div>
      )}
    </div>
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
      setActiveEdges(prev => {
        const next = new Set(prev)
        next.add(id)
        return next
      })
      const timeoutId = setTimeout(() => {
        setActiveEdges(s => { const n = new Set(s); n.delete(id); return n })
        timeoutIds.delete(timeoutId)
      }, 2200)
      timeoutIds.add(timeoutId)
    }, 700)
    return () => {
      clearInterval(iv)
      timeoutIds.forEach(clearTimeout)
    }
  }, [])

  const handleWheel = useCallback((e) => {
    e.preventDefault()
    setZoom(z => Math.max(0.5, Math.min(2.5, z - e.deltaY * 0.001)))
  }, [])

  const handleMouseDown = useCallback((e) => {
    if (e.target === svgRef.current || e.target.tagName === 'svg') {
      setIsPanning(true)
      panStart.current = { x: e.clientX - pan.x, y: e.clientY - pan.y }
    }
  }, [pan])

  const handleMouseMove = useCallback((e) => {
    if (!isPanning || !panStart.current) return
    setPan({ x: e.clientX - panStart.current.x, y: e.clientY - panStart.current.y })
  }, [isPanning])

  const handleMouseUp = useCallback(() => setIsPanning(false), [])

  const nodeMap = Object.fromEntries(GRAPH_NODES.map(n => [n.id, n]))
  const riskCounts = GRAPH_NODES.reduce((acc, n) => { acc[n.risk] = (acc[n.risk] || 0) + 1; return acc }, {})

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden', background: 'var(--bg-base)' }}>

      {/* Header */}
      <div style={{
        padding: '16px 24px',
        borderBottom: '1px solid var(--border)',
        background: 'var(--bg-card)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        flexShrink: 0,
        boxShadow: 'var(--shadow-sm)',
      }}>
        <div>
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.3px' }}>
            Graph Explorer
          </h2>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 1 }}>
            {GRAPH_NODES.length} nodes · {GRAPH_EDGES.length} edges
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          {/* Legend */}
          {Object.entries(RISK_COLORS).map(([risk, color]) => (
            <div key={risk} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: color, border: `1.5px solid ${color}`, opacity: 0.85 }} />
              <span style={{ fontSize: 11, color: 'var(--text-secondary)', textTransform: 'capitalize' }}>
                {risk}{' '}
                <span style={{ color: 'var(--text-faint)', fontFamily: 'var(--font-mono)', fontSize: 10 }}>
                  ({riskCounts[risk] || 0})
                </span>
              </span>
            </div>
          ))}
          <div style={{ width: 1, height: 14, background: 'var(--border)' }} />
          {/* Zoom */}
          {[['−', -0.2], ['+', 0.2]].map(([lbl, d]) => (
            <button key={lbl}
              onClick={() => setZoom(z => Math.max(0.5, Math.min(2.5, z + d)))}
              style={{
                width: 26, height: 26, borderRadius: 'var(--radius-sm)',
                background: 'var(--bg-subtle)', border: '1px solid var(--border)',
                color: 'var(--text-secondary)', fontSize: 15,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
              {lbl}
            </button>
          ))}
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 11,
            color: 'var(--text-muted)', padding: '3px 8px',
            background: 'var(--bg-subtle)', border: '1px solid var(--border)',
            borderRadius: 4,
          }}>
            {Math.round(zoom * 100)}%
          </span>
        </div>
      </div>

      {/* Canvas */}
      <div
        style={{
          flex: 1, position: 'relative', overflow: 'hidden',
          background: 'var(--bg-base)',
          cursor: isPanning ? 'grabbing' : 'grab',
        }}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        {/* Subtle dot grid */}
        <div style={{
          position: 'absolute', inset: 0, pointerEvents: 'none',
          backgroundImage: 'radial-gradient(circle, var(--border-strong) 1px, transparent 1px)',
          backgroundSize: '28px 28px',
          opacity: 0.5,
        }} />

        <svg
          ref={svgRef}
          width="100%" height="100%"
          viewBox="0 0 560 500"
          style={{
            transform: `scale(${zoom}) translate(${pan.x / zoom}px, ${pan.y / zoom}px)`,
            transformOrigin: 'center',
            transition: isPanning ? 'none' : 'transform 0.05s',
            overflow: 'visible',
          }}
        >
          <ArrowMarkers />

          <g>
            {GRAPH_EDGES.map(edge => {
              const from = nodeMap[edge.from]
              const to = nodeMap[edge.to]
              if (!from || !to) return null
              return (
                <GraphEdge
                  key={edge.id}
                  edge={edge}
                  fromNode={from}
                  toNode={to}
                  isActive={activeEdges.has(edge.id)}
                />
              )
            })}
          </g>

          <g>
            {GRAPH_NODES.map((node, i) => (
              <GraphNode
                key={node.id}
                node={node}
                isSelected={selectedNode?.id === node.id}
                onSelect={n => setSelectedNode(prev => prev?.id === n.id ? null : n)}
                delay={i * 55}
              />
            ))}
          </g>
        </svg>

        {/* Live metrics overlay */}
        <div style={{
          position: 'absolute', top: 14, left: 14,
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          padding: '12px 14px',
          boxShadow: 'var(--shadow-md)',
          minWidth: 148,
        }}>
          <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', marginBottom: 10 }}>
            Live Metrics
          </div>
          {[
            ['Active edges', `${activeEdges.size} / ${GRAPH_EDGES.length}`],
            ['Total flow',   '$8.42M'],
            ['Avg hop time', '4.2 min'],
          ].map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 5 }}>
              <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{k}</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-primary)', fontWeight: 600 }}>{v}</span>
            </div>
          ))}
        </div>

        {/* Cycle alert */}
        <div style={{
          position: 'absolute', bottom: 14, left: 14,
          background: 'var(--risk-critical-bg)',
          border: '1px solid var(--risk-critical-border)',
          borderLeft: '3px solid var(--risk-critical)',
          borderRadius: 'var(--radius-sm)',
          padding: '8px 12px',
          display: 'flex', alignItems: 'center', gap: 8,
          boxShadow: 'var(--shadow-sm)',
        }}>
          <div style={{
            width: 6, height: 6, borderRadius: '50%',
            background: 'var(--risk-critical)',
            animation: 'subtlePulse 1.5s ease-in-out infinite',
          }} />
          <span style={{ fontSize: 11, color: 'var(--risk-critical)', fontWeight: 500 }}>
            Cycle: ACC-4471 → EXC-0044 → ACC-6612 → ACC-4471
          </span>
        </div>

        <NodePanel node={selectedNode} onClose={() => setSelectedNode(null)} />
      </div>
    </div>
  )
}
