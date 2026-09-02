import React, { useState, useEffect, useCallback } from 'react';
import { GraphCanvas } from './GraphCanvas';
import { InspectorSidebar } from './InspectorSidebar';
import { apiClient } from '../services/api';

export default function GraphExplorer({ onNav, navContext }) {
  const [elements, setElements] = useState({ nodes: [], edges: [] });
  const [selectedNode, setSelectedNode] = useState(null);
  const [searchAccount, setSearchAccount] = useState('');
  const [depth, setDepth] = useState(2);
  const [loading, setLoading] = useState(false);

  const fetchGraph = useCallback(async (accountId, hopDepth) => {
    if (!accountId) return;
    setLoading(true);
    try {
      const data = await apiClient.getSubgraph(accountId.trim(), hopDepth);
      setElements(data);
      if (data.nodes?.length > 0) {
        const match = data.nodes.find((n) => n.data.id === accountId.trim());
        setSelectedNode(match ? match.data : data.nodes[0].data);
      }
    } catch (err) {
      console.error('Failed to load subgraph:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const targetId = navContext?.accountId || navContext?.id;
    if (targetId) {
      setSearchAccount(targetId);
      fetchGraph(targetId, depth);
    }
  }, [navContext, depth, fetchGraph]);

  const handleSearch = (e) => {
    e.preventDefault();
    if (searchAccount.trim()) {
      fetchGraph(searchAccount, depth);
    }
  };

  const handleNodeUpdated = (updatedNodeData) => {
    setSelectedNode(updatedNodeData);
    setElements((prev) => ({
      ...prev,
      nodes: prev.nodes.map((node) =>
        node.data.id === updatedNodeData.id
          ? { ...node, data: { ...node.data, ...updatedNodeData } }
          : node
      )
    }));
  };
  return (
    <div className="flex h-full w-full bg-slate-950 text-slate-100 overflow-hidden">
      <div className="flex-1 flex flex-col h-full min-w-0">
        <header className="h-14 bg-slate-900/80 backdrop-blur border-b border-slate-800 px-6 flex items-center justify-between z-10">
          <div className="flex items-center space-x-3">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Live Topology Viewer
            </span>
          </div>

          <form onSubmit={handleSearch} className="flex items-center space-x-2">
            <input
              type="text"
              placeholder="Search account ID / hash..."
              value={searchAccount}
              onChange={(e) => setSearchAccount(e.target.value)}
              className="bg-slate-950 border border-slate-700 px-3 py-1.5 rounded text-xs w-72 focus:outline-none focus:border-sky-500 font-mono text-slate-200"
            />
            <select
              value={depth}
              onChange={(e) => setDepth(Number(e.target.value))}
              className="bg-slate-950 border border-slate-700 px-2 py-1.5 rounded text-xs focus:outline-none text-slate-300"
            >
              <option value={1}>1-hop</option>
              <option value={2}>2-hop</option>
              <option value={3}>3-hop</option>
            </select>
            <button
              type="submit"
              disabled={loading}
              className="bg-sky-600 hover:bg-sky-500 disabled:bg-slate-800 px-3.5 py-1.5 rounded text-xs font-semibold text-white transition-colors"
            >
              {loading ? 'Fetching...' : 'Traverse'}
            </button>
          </form>
        </header>
        <div className="flex-1 relative min-h-0">
          <GraphCanvas elements={elements} onSelectNode={setSelectedNode} />

          {(!elements.nodes || elements.nodes.length === 0) && !loading && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <p className="text-slate-600 text-xs tracking-wide">
                Search an account hash to inspect graph flow topology
              </p>
            </div>
          )}
        </div>
      </div>

      <InspectorSidebar
        node={selectedNode}
        onClose={() => setSelectedNode(null)}
        onNodeUpdated={handleNodeUpdated}
      />
    </div>
  );
}