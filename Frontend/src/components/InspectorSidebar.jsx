import React, { useEffect, useState } from 'react';
import { apiClient } from '../services/api';

const needsEnrichment = (node) => Boolean(node?.risk_tier) && node.risk_tier !== 'low';

export const InspectorSidebar = ({ node, onClose, onNodeUpdated }) => {
  const [report, setReport] = useState(null);
  const [verdict, setVerdict] = useState(null);
  const [evaluating, setEvaluating] = useState(false);
  const [enriching, setEnriching] = useState(false);

  useEffect(() => {
    if (!node) {
      setVerdict(null);
      setReport(null);
      return;
    }

    let active = true;

    // 1. Run Stage 06/07 Risk Aggregation evaluation
    setEvaluating(true);
    apiClient.evaluateRisk(node.id, { gnn_score: node.risk_score || 0.5 })
      .then((res) => {
        if (active) setVerdict(res);
      })
      .catch((err) => console.error('Aggregation evaluation failed:', err))
      .finally(() => {
        if (active) setEvaluating(false);
      });

    // 2. Fetch Claude AI report for elevated risk accounts
    if (needsEnrichment(node)) {
      setEnriching(true);
      apiClient.getAIReport(node.id)
        .then((data) => {
          if (active) setReport({ accountId: node.id, data });
        })
        .catch((err) => console.error('AI enrichment failed:', err))
        .finally(() => {
          if (active) setEnriching(false);
        });
    } else {
      setReport(null);
      setEnriching(false);
    }

    return () => {
      active = false;
    };
  }, [node]);

  if (!node) return null;

  const currentTier = verdict ? verdict.risk_tier : node.risk_tier || 'low';
  const currentScore = verdict ? verdict.risk_score : node.risk_score || 0;
  const settledForNode = Boolean(report && report.accountId === node.id);
  const currentReport = settledForNode ? report.data : null;

  const getTierBadgeStyle = (tier) => {
    switch (tier) {
      case 'critical': return 'text-red-400 bg-red-950/60 border-red-800';
      case 'high': return 'text-orange-400 bg-orange-950/60 border-orange-800';
      case 'medium': return 'text-yellow-400 bg-yellow-950/60 border-yellow-800';
      default: return 'text-emerald-400 bg-emerald-950/60 border-emerald-800';
    }
  };

  const handleSimulateCycle = async () => {
    setEvaluating(true);
    try {
      const res = await apiClient.evaluateRisk(node.id, {
        gnn_score: currentScore,
        has_cycle: true,
        cycle_length: 3
      });
      setVerdict(res);
      if (onNodeUpdated) {
        onNodeUpdated({ ...node, risk_score: res.risk_score, risk_tier: res.risk_tier });
      }
    } catch (err) {
      console.error('Cycle simulation failed:', err);
    } finally {
      setEvaluating(false);
    }
  };

  return (
    <aside className="w-96 bg-slate-900 border-l border-slate-800 p-6 flex flex-col h-full overflow-y-auto shadow-2xl">
      {/* Header */}
      <div className="flex justify-between items-center pb-4 border-b border-slate-800">
        <h2 className="text-lg font-semibold text-slate-100">Account Decision Audit</h2>
        <button 
          onClick={onClose} 
          className="text-slate-400 hover:text-white transition-colors"
        >
          ✕
        </button>
      </div>

      <div className="mt-4 space-y-4">
        {/* Account Identifier */}
        <div>
          <span className="text-xs text-slate-400 uppercase tracking-wider">Account Key</span>
          <p className="font-mono text-xs text-slate-200 break-all mt-1 bg-slate-950 p-2 rounded border border-slate-800">
            {node.id}
          </p>
        </div>

        {/* Aggregated Score & Tier Display */}
        <div className="grid grid-cols-2 gap-3">
          <div className="bg-slate-950 p-3 rounded border border-slate-800">
            <span className="text-xs text-slate-400">Aggregated Risk</span>
            <p className="text-xl font-bold text-slate-100 mt-1">
              {(currentScore * 100).toFixed(1)}%
            </p>
          </div>
          <div className="bg-slate-950 p-3 rounded border border-slate-800">
            <span className="text-xs text-slate-400">Tier</span>
            <div className={`mt-1 inline-block px-2 py-0.5 rounded text-xs font-semibold border ${getTierBadgeStyle(currentTier)}`}>
              {currentTier.toUpperCase()}
            </div>
          </div>
        </div>

        {/* Classifier Confidence & AI Delegation Status */}
        {verdict && (
          <div className="bg-slate-950 p-3.5 rounded border border-slate-800 space-y-2">
            <div className="flex justify-between items-center text-xs">
              <span className="text-slate-400">Classifier Confidence</span>
              <span className="font-semibold text-slate-200">{(verdict.confidence * 100).toFixed(0)}%</span>
            </div>
            
            <div className="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
              <div 
                className={`h-1.5 rounded-full ${verdict.confidence >= 0.7 ? 'bg-sky-500' : 'bg-amber-500'}`}
                style={{ width: `${verdict.confidence * 100}%` }}
              />
            </div>

            {verdict.delegated_to_ai ? (
              <div className="mt-2 flex items-center gap-1.5 text-[11px] font-medium text-amber-300 bg-amber-950/40 border border-amber-800/60 p-2 rounded">
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping" />
                Delegated to Claude API (Low Confidence Edge Case)
              </div>
            ) : (
              <div className="mt-1 text-[11px] text-slate-500">
                Resolved deterministically via Stage 05/06 scoring
              </div>
            )}
          </div>
        )}

        {/* Input Signals Breakdown */}
        {verdict?.triggering_signals && (
          <div>
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Input Signals
            </span>
            <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
              <div className="bg-slate-950/70 p-2 rounded border border-slate-800">
                <span className="text-slate-500 block">GNN Weight (75%)</span>
                <span className="font-mono text-slate-200">
                  {verdict.triggering_signals.gnn_score?.toFixed(3)}
                </span>
              </div>
              <div className="bg-slate-950/70 p-2 rounded border border-slate-800">
                <span className="text-slate-500 block">Cycle Detection</span>
                <span className={`font-mono ${verdict.triggering_signals.has_cycle ? 'text-red-400' : 'text-slate-400'}`}>
                  {verdict.triggering_signals.has_cycle ? `${verdict.triggering_signals.cycle_length}-Hop Loop` : 'None'}
                </span>
              </div>
              <div className="bg-slate-950/70 p-2 rounded border border-slate-800">
                <span className="text-slate-500 block">PageRank (10%)</span>
                <span className="font-mono text-slate-200">
                  {verdict.triggering_signals.pagerank_percentile?.toFixed(2)}
                </span>
              </div>
              <div className="bg-slate-950/70 p-2 rounded border border-slate-800">
                <span className="text-slate-500 block">Community (15%)</span>
                <span className="font-mono text-slate-200">
                  {verdict.triggering_signals.community_risk_score?.toFixed(2)}
                </span>
              </div>
            </div>
          </div>
        )}

        {/* AI Explainability & Audit Trail Section */}
        <div className="mt-4 pt-4 border-t border-slate-800">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold text-sky-400 uppercase tracking-wider">
              Compliance Reasoning
            </h3>
            {(enriching || evaluating) && (
              <span className="text-[11px] text-slate-400 animate-pulse">Analyzing signals...</span>
            )}
          </div>

          {currentReport ? (
            <div className="mt-2 bg-slate-950 rounded p-3 border border-slate-800 space-y-2">
              <div>
                <span className="text-[10px] text-slate-400 uppercase">Detected Typology</span>
                <p className="text-xs font-medium text-amber-300">
                  {currentReport.detected_typology || 'None detected'}
                </p>
              </div>
              <div>
                <span className="text-[10px] text-slate-400 uppercase">Reasoning</span>
                <p className="text-xs text-slate-300 mt-0.5 leading-relaxed">
                  {currentReport.explanation}
                </p>
              </div>
              <div className="pt-2 border-t border-slate-800">
                <span className="text-[10px] text-slate-400 uppercase">Audit Trail</span>
                <p className="text-[11px] font-mono text-slate-400 mt-0.5 bg-slate-900 p-2 rounded">
                  {currentReport.compliance_summary}
                </p>
              </div>
            </div>
          ) : verdict?.explanation ? (
            <p className="text-xs text-slate-300 mt-2 bg-slate-950 p-3 rounded border border-slate-800 leading-relaxed font-sans">
              {verdict.explanation}
            </p>
          ) : !needsEnrichment(node) ? (
            <p className="mt-2 text-xs text-slate-500 leading-relaxed">
              Automated AI enrichment runs for elevated-risk accounts (medium tier and above).
            </p>
          ) : null}
        </div>

        {/* Override Simulation for Testing */}
        <div className="pt-2 border-t border-slate-800">
          <button
            onClick={handleSimulateCycle}
            disabled={evaluating}
            className="w-full bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-xs py-2 rounded text-slate-200 transition-colors border border-slate-700 font-medium"
          >
            {evaluating ? 'Evaluating...' : 'Simulate 3-Hop Cycle Override'}
          </button>
        </div>
      </div>
    </aside>
  );
};