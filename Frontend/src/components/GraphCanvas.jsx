import { useEffect, useRef } from 'react'
import cytoscape from 'cytoscape'
import coseBilkent from 'cytoscape-cose-bilkent'
import { buildStyle, communityColor, gnnHeat, isMarked, layoutFor, prSize } from '../lib/graphStyle'

try { cytoscape.use(coseBilkent) } catch { /* registered under HMR already */ }

const LABEL_LIMIT = 200

function decorate(elements, cutoff) {
  const prs = elements.nodes.map(n => n.data.pagerank_score || 0)
  const min = prs.length ? Math.min(...prs) : 0, max = prs.length ? Math.max(...prs) : 0
  const nodes = elements.nodes.map(n => ({ ...n, data: {
    ...n.data,
    _size: prSize(n.data.pagerank_score || 0, min, max),
    _comm: communityColor(n.data.community_id),
    _heat: gnnHeat(n.data.gnn_risk_score),
    _marked: isMarked(n.data, cutoff),
  } }))
  return [...nodes, ...(elements.edges || [])]
}

/**
 * Light-themed Cytoscape canvas. `lens` picks the colouring; `cutoff` drives the
 * marked lens; `highlightIds` (e.g. a shortest path) are emphasised.
 */
export function GraphCanvas({ elements, lens = 'risk', cutoff = 0.74, selectedId, onSelectNode, highlightIds }) {
  const containerRef = useRef(null)
  const cyRef = useRef(null)

  // (re)build on new elements
  useEffect(() => {
    if (!containerRef.current) return
    const n = elements.nodes?.length || 0
    const cy = cytoscape({
      container: containerRef.current,
      elements: decorate(elements, cutoff),
      style: buildStyle(lens, { labels: n <= LABEL_LIMIT }),
      minZoom: 0.15,
      maxZoom: 3,
    })
    // Run the layout explicitly so we can clamp the zoom afterwards: `fit` on a
    // two-node neighbourhood otherwise blows the nodes up to fill the canvas.
    const layout = cy.layout(layoutFor(n))
    layout.one('layoutstop', () => {
      cy.fit(undefined, 48)
      if (cy.zoom() > 1.25) { cy.zoom(1.25); cy.center() }
    })
    layout.run()
    cy.on('tap', 'node', evt => onSelectNode?.(evt.target.data()))
    cy.on('tap', evt => { if (evt.target === cy) onSelectNode?.(null) })
    if (n <= 800) {
      cy.on('mouseover', 'node', e => {
        cy.elements().addClass('faded'); e.target.closedNeighborhood().removeClass('faded')
        e.target.addClass('hl'); e.target.connectedEdges().addClass('hl')
      })
      cy.on('mouseout', 'node', () => cy.elements().removeClass('faded hl'))
    }
    cyRef.current = cy
    return () => { cy.destroy(); cyRef.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements])

  // restyle without relayout
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.batch(() => cy.nodes().forEach(nd => nd.data('_marked', isMarked(nd.data(), cutoff))))
    cy.style(buildStyle(lens, { labels: cy.nodes().length <= LABEL_LIMIT }))
  }, [lens, cutoff])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.elements().unselect()
    if (selectedId) cy.getElementById(selectedId).select()
  }, [selectedId])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.elements().removeClass('hl faded')
    if (highlightIds?.length) {
      const set = new Set(highlightIds)
      cy.elements().addClass('faded')
      cy.nodes().filter(nd => set.has(nd.id())).removeClass('faded').addClass('hl')
      cy.edges().filter(e => set.has(e.source().id()) && set.has(e.target().id())).removeClass('faded').addClass('hl')
    }
  }, [highlightIds])

  return <div ref={containerRef} className="h-full w-full bg-base" />
}
