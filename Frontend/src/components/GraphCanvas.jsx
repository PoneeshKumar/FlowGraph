import React, { useEffect, useRef } from 'react';
import cytoscape from 'cytoscape';
import coseBilkent from 'cytoscape-cose-bilkent';

try {
  cytoscape.use(coseBilkent);
} catch (e) {
  // Prevent duplicate extension registration in HMR
}

export const GraphCanvas = ({ elements, onSelectNode }) => {
  const containerRef = useRef(null);
  const cyRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return;

    cyRef.current = cytoscape({
      container: containerRef.current,
      elements: [...(elements.nodes || []), ...(elements.edges || [])],
      style: [
        {
          selector: 'node',
          style: {
            'label': 'data(label)',
            'color': '#cbd5e1',
            'font-size': '11px',
            'text-valign': 'bottom',
            'text-margin-y': 4,
            'background-color': '#475569',
            'width': '36px',
            'height': '36px',
            'border-width': 2,
            'border-color': '#1e293b'
          }
        },
        {
          selector: 'node[risk_tier = "critical"]',
          style: { 'background-color': '#ef4444', 'border-color': '#fee2e2', 'border-width': 3 }
        },
        {
          selector: 'node[risk_tier = "high"]',
          style: { 'background-color': '#f97316', 'border-color': '#ffedd5' }
        },
        {
          selector: 'node[risk_tier = "medium"]',
          style: { 'background-color': '#eab308' }
        },
        {
          selector: 'node[risk_tier = "low"]',
          style: { 'background-color': '#10b981' }
        },
        {
          selector: 'edge',
          style: {
            'width': 'mapData(weight, 1, 10, 1.5, 6)',
            'line-color': '#475569',
            'target-arrow-color': '#475569',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'arrow-scale': 0.9,
            'opacity': 0.7
          }
        },
        {
          selector: ':selected',
          style: {
            'border-width': 4,
            'border-color': '#38bdf8',
            'line-color': '#38bdf8',
            'target-arrow-color': '#38bdf8'
          }
        }
      ],
      layout: {
        name: 'cose-bilkent',
        animate: false,
        nodeDimensionsIncludeLabels: true,
        idealEdgeLength: 100,
        nodeRepulsion: 4500
      }
    });

    cyRef.current.on('tap', 'node', (evt) => {
      onSelectNode(evt.target.data());
    });

    cyRef.current.on('tap', (evt) => {
      if (evt.target === cyRef.current) {
        onSelectNode(null);
      }
    });

    return () => {
      cyRef.current?.destroy();
    };
  }, [elements, onSelectNode]);

  return <div ref={containerRef} className="w-full h-full bg-slate-950" />;
};