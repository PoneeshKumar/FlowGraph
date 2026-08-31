import React, { useEffect, useState } from 'react';
import { apiClient } from '../services/api';

export const InspectorSidebar = ({ node, onClose }) => {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!node) {
      setReport(null);
      return;
    }
    setLoading(true);
    apiClient.getAIReport(node.id)
      .then(setReport)
      .catch((err) => console.error('Enrichment fetch failed:', err))
      .finally(() => setLoading(false));
  }, [node]);

  if (!node) return null;

  const getTierBadgeStyle = (tier) => {
    switch (tier) {
      case 'critical': return 'text-red-400 bg-red-950/60 border-red-800';
      case 'high': return 'text-orange-400 bg-orange-950/60 border-orange-800';
      case 'medium': return 'text-yellow-400 bg-yellow-950/60 border-yellow-800';
      default: return 'text-emerald-400 bg-emerald-950/60 border-emerald-800';
    }
  };

  return (
    <aside className="w-96 bg-slate-900 border-l border-slate-800 p-6 flex flex-col h-full overflow-y-auto shadow-2xl">
      <div className="flex justify-between items-center pb-4 border-b border-slate-800">
        <h2 className="text-lg font-semibold text-slate-100">Account Audit</h2>
        <button 
          onClick={onClose} 
          className="text-slate-400 hover:text-white transition-colors"
        >
          ✕
        </button>
      </div>

      <div className="mt-4 space-y-4">
        <div>
          <span className="text-xs text-slate-400 uppercase tracking-wider">Account Key</span>
          <p className="font-mono text-xs text-slate-200 break-all mt-1 bg-slate-950 p-2 rounded border border-slate-800">
            {node.id}
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="bg-slate-950 p-3 rounded border border-slate-800">
            <span className="text-xs text-slate-400">Risk Score</span>
            <p className="text-xl font-bold text-slate-100 mt-1">
              {((node.risk_score || 0) * 100).toFixed(1)}%
            </p>
          </div>
          <div className="bg-slate-950 p-3 rounded border border-slate-800">
            <span className="text-xs text-slate-400">Tier</span>
            <div className={`mt-1 inline-block px-2 py-0.5 rounded text-xs font-semibold border ${getTierBadgeStyle(node.risk_tier)}`}>
              {(node.risk_tier || 'low').toUpperCase()}
            </div>
          </div>
        </div>

        {/* AI Explainability Audit Section */}
        <div className="mt-6">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-sky-400 uppercase tracking-wider">
              AI Explainability Audit
            </h3>
            {loading && <span className="text-xs text-slate-400 animate-pulse">Evaluating graph...</span>}
          </div>

          {report && !loading && (
            <div className="mt-3 bg-slate-950 rounded-lg p-4 border border-slate-800 space-y-3">
              <div>
                <span className="text-xs text-slate-400">Detected Typology</span>
                <p className="text-sm font-medium text-amber-300">
                  {report.detected_typology || 'None detected'}
                </p>
              </div>
              <div>
                <span className="text-xs text-slate-400">Reasoning</span>
                <p className="text-xs text-slate-300 mt-1 leading-relaxed">
                  {report.explanation}
                </p>
              </div>
              <div className="pt-2 border-t border-slate-800">
                <span className="text-xs text-slate-400">Regulatory Audit Trail</span>
                <p className="text-xs font-mono text-slate-400 mt-1 bg-slate-900 p-2 rounded">
                  {report.compliance_summary}
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
};