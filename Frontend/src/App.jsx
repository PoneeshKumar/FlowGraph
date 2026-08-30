import React, { useState } from 'react';
import { GraphCanvas } from './components/GraphCanvas';
import { InspectorSidebar } from './components/InspectorSidebar';
import { apiClient } from './services/api';

export const App = () => {
  const [elements, setElements] = useState({ nodes: [], edges: [] });
  const [selectedNode, setSelectedNode] = useState(null);
  const [searchAccount, setSearchAccount] = useState('');
  const [depth, setDepth] = useState(2);
  const [loading, setLoading] = useState(false);

  const handleSearch = async (e) => {
    e.preventDefault();
    if (!searchAccount.trim()) return;
    setLoading(true);
    try {
      const data = await apiClient.getSubgraph(searchAccount.trim(), depth);
      setElements(data);
      if (data.nodes && data.nodes.length > 0) {
        setSelectedNode(data.nodes[0].data);
      }
    } catch (err) {
      console.error('Failed to fetch subgraph:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex h-screen w-screen bg-slate-950 text-slate-100 font-sans overflow-hidden">
      <div className="flex-1 flex flex-col h-full">
        {/* Top Navigation / Controls */}
        <header className="h-16 bg-slate-900 border-b border-slate-800 px-6 flex items-center justify-between z-10">
          <div className="flex items-center space-x-3">
            <div className="w-3 h-3 rounded-full bg-sky-500 animate-pulse" />
            <h1 className="font-bold text-base tracking-wide bg-gradient-to-r from-sky-400 to-indigo-400 bg-clip-text text-transparent">
              FlowGraph Intelligence
            </h1>
          </div>

          <form onSubmit={handleSearch} className="flex items-center space-x-3">
            <input
              type="text"
              placeholder="Search account ID / hash..."
              value={searchAccount}
              onChange={(e) => setSearchAccount(e.target.value)}
              className="bg-slate-950 border border-slate-700 px-3 py-1.5 rounded text-xs w-80 focus:outline-none focus:border-sky-500 font-mono text-slate-200"
            />
            <select
              value={depth}
              onChange={(e) => setDepth(Number(e.target.value))}
              className="bg-slate-950 border border-slate-700 px-2 py-1.5 rounded text-xs focus:outline-none text-slate-200"
            >
              <option value={1}>1-hop</option>
              <option value={2}>2-hop</option>
              <option value={3}>3-hop</option>
            </select>
            <button
              type="submit"
              disabled={loading}
              className="bg-sky-600 hover:bg-sky-500 disabled:bg-slate-800 px-4 py-1.5 rounded text-xs font-semibold text-white transition-colors"
            >
              {loading ? 'Searching...' : 'Explore Graph'}
            </button>
          </form>
        </header>

        {/* Graph Visualizer Canvas */}
        <main className="flex-1 relative">
          <GraphCanvas elements={elements} onSelectNode={setSelectedNode} />
          {(!elements.nodes || elements.nodes.length === 0) && !loading && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <p className="text-slate-600 text-sm">
                Enter an account hash above to explore real-time flow topologies
              </p>
            </div>
          )}
        </main>
      </div>

      {/* Inspector Drawer */}
      <InspectorSidebar node={selectedNode} onClose={() => setSelectedNode(null)} />
    </div>
  );
};

export default App;