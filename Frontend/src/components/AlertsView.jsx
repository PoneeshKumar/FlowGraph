import { useState } from 'react'
import { RECENT_ALERTS } from '../data/mockData'

const MORE_ALERTS = [
  ...RECENT_ALERTS,
  {
    id: 'ALT-006', severity: 'medium', type: 'Velocity Anomaly',
    message: 'BNK-1102 sent 12 transactions in 30 min — 6× baseline',
    account: 'BNK-1102', amount: 380000, timestamp: '2 hr ago', confidence: 68,
    aiExplanation: 'Transaction velocity for BNK-1102 is 6× its 30-day average. No prior suspicious activity on record, but pattern warrants monitoring.',
  },
  {
    id: 'ALT-007', severity: 'low', type: 'New Account Flag',
    message: 'EXC-0044 — created 6 days ago, already $1.55M in volume',
    account: 'EXC-0044', amount: 1550000, timestamp: '3 hr ago', confidence: 55,
    aiExplanation: 'EXC-0044 was created 6 days ago and has already processed $1.55M in transactions. Rapid ramp-up is a soft signal for shell account behavior.',
  },
]

const RISK_MAP = {
  critical: { color: 'var(--risk-critical)', bg: 'var(--risk-critical-bg)', border: 'var(--risk-critical-border)' },
  high:     { color: 'var(--risk-high)',     bg: 'var(--risk-high-bg)',     border: 'var(--risk-high-border)'     },
  medium:   { color: 'var(--risk-medium)',   bg: 'var(--risk-medium-bg)',   border: 'var(--risk-medium-border)'   },
  low:      { color: 'var(--risk-low)',      bg: 'var(--risk-low-bg)',      border: 'var(--risk-low-border)'      },
}

export default function AlertsView() {
  const [selected, setSelected] = useState(null)
  const [filter, setFilter] = useState('all')

  const filtered = MORE_ALERTS.filter(a => filter === 'all' || a.severity === filter)
  const counts = MORE_ALERTS.reduce((acc, a) => { acc[a.severity] = (acc[a.severity] || 0) + 1; return acc }, {})

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'auto', background: 'var(--bg-base)' }}>

      {/* Header */}
      <div style={{
        padding: '24px 28px 0',
        background: 'var(--bg-card)',
        borderBottom: '1px solid var(--border)',
        boxShadow: 'var(--shadow-sm)',
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16 }}>
          <div>
            <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 20, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.3px', marginBottom: 3 }}>
              Risk Alerts
            </h1>
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              AI-generated explanations for every flag
            </div>
          </div>
          <button style={{
            padding: '7px 14px', fontSize: 12, fontWeight: 500,
            background: 'var(--bg-subtle)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)', color: 'var(--text-secondary)',
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M2 9.5h8M2 6h8M2 2.5h8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
            </svg>
            Export Report
          </button>
        </div>

        {/* Filter tabs */}
        <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--border)', marginBottom: -1 }}>
          {[
            ['all',      'All',      MORE_ALERTS.length, 'var(--text-primary)'],
            ['critical', 'Critical', counts.critical || 0, 'var(--risk-critical)'],
            ['high',     'High',     counts.high || 0,     'var(--risk-high)'],
            ['medium',   'Medium',   counts.medium || 0,   'var(--risk-medium)'],
            ['low',      'Low',      counts.low || 0,      'var(--risk-low)'],
          ].map(([f, label, count, color]) => {
            const isActive = filter === f
            return (
              <button
                key={f}
                onClick={() => setFilter(f)}
                style={{
                  padding: '9px 16px',
                  fontSize: 12, fontWeight: isActive ? 600 : 400,
                  color: isActive ? color : 'var(--text-muted)',
                  background: 'transparent',
                  border: 'none',
                  borderBottom: isActive ? `2px solid ${color}` : '2px solid transparent',
                  display: 'flex', alignItems: 'center', gap: 6,
                  transition: 'all 0.13s',
                  marginBottom: -1,
                }}
              >
                {label}
                <span style={{
                  fontSize: 10, fontFamily: 'var(--font-mono)',
                  background: isActive ? RISK_MAP[f]?.bg || 'var(--bg-subtle)' : 'var(--bg-subtle)',
                  color: isActive ? color : 'var(--text-faint)',
                  border: `1px solid ${isActive ? RISK_MAP[f]?.border || 'var(--border)' : 'var(--border)'}`,
                  padding: '1px 5px', borderRadius: 99,
                }}>
                  {count}
                </span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Content */}
      <div style={{ padding: '22px 28px', flex: 1 }}>
        <div style={{
          display: 'grid',
          gridTemplateColumns: selected ? '1fr 360px' : '1fr',
          gap: 18, alignItems: 'start',
        }}>

          {/* List */}
          <div>
            {filtered.map((alert, i) => {
              const s = RISK_MAP[alert.severity]
              const isSelected = selected?.id === alert.id
              return (
                <div
                  key={alert.id}
                  onClick={() => setSelected(prev => prev?.id === alert.id ? null : alert)}
                  style={{
                    background: isSelected ? s.bg : 'var(--bg-card)',
                    border: `1px solid ${isSelected ? s.border : 'var(--border)'}`,
                    borderLeft: `3px solid ${isSelected ? s.color : 'transparent'}`,
                    borderRadius: 'var(--radius)',
                    padding: '16px 18px',
                    marginBottom: 10,
                    cursor: 'pointer',
                    transition: 'all 0.13s',
                    boxShadow: 'var(--shadow-sm)',
                    animation: `fadeSlideIn 0.28s ease ${i * 45}ms both`,
                  }}
                  onMouseEnter={e => { if (!isSelected) { e.currentTarget.style.background = 'var(--bg-hover)'; e.currentTarget.style.borderLeftColor = s.color + '60' } }}
                  onMouseLeave={e => { if (!isSelected) { e.currentTarget.style.background = 'var(--bg-card)'; e.currentTarget.style.borderLeftColor = 'transparent' } }}
                >
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14 }}>
                    {/* Severity dot container */}
                    <div style={{
                      width: 34, height: 34, borderRadius: 8, flexShrink: 0,
                      background: s.bg, border: `1px solid ${s.border}`,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                      <div style={{ width: 9, height: 9, borderRadius: '50%', background: s.color }} />
                    </div>

                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                        <span style={{
                          fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
                          letterSpacing: '0.07em', color: s.color,
                        }}>
                          {alert.severity}
                        </span>
                        <span style={{
                          fontFamily: 'var(--font-display)', fontSize: 13, fontWeight: 600,
                          color: 'var(--text-primary)',
                        }}>
                          {alert.type}
                        </span>
                        <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text-faint)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>
                          {alert.id}
                        </span>
                      </div>

                      <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: 10 }}>
                        {alert.message}
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{
                          fontSize: 11, fontFamily: 'var(--font-mono)',
                          background: 'var(--bg-subtle)', border: '1px solid var(--border)',
                          padding: '2px 7px', borderRadius: 3, color: 'var(--text-secondary)',
                        }}>{alert.account}</span>
                        <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--text-primary)' }}>
                          ${(alert.amount / 1000).toFixed(0)}K
                        </span>
                        <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>·</span>
                        <span style={{ fontSize: 11, color: s.color }}>
                          {alert.confidence}% confidence
                        </span>
                        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-faint)' }}>
                          {alert.timestamp}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Detail panel */}
          {selected && (() => {
            const s = RISK_MAP[selected.severity]
            return (
              <div style={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border)',
                borderTop: `3px solid ${s.color}`,
                borderRadius: 'var(--radius)',
                padding: '20px',
                position: 'sticky', top: 22,
                boxShadow: 'var(--shadow-md)',
                animation: 'fadeSlideIn 0.18s ease',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
                  <div>
                    <div style={{ fontSize: 10, color: 'var(--text-faint)', fontFamily: 'var(--font-mono)', marginBottom: 4 }}>
                      {selected.id}
                    </div>
                    <div style={{ fontFamily: 'var(--font-display)', fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>
                      {selected.type}
                    </div>
                  </div>
                  <button onClick={() => setSelected(null)} style={{
                    width: 22, height: 22, borderRadius: '50%',
                    background: 'var(--bg-subtle)', border: '1px solid var(--border)',
                    color: 'var(--text-muted)', display: 'flex',
                    alignItems: 'center', justifyContent: 'center', fontSize: 14,
                  }}>×</button>
                </div>

                {/* Confidence bar */}
                <div style={{ marginBottom: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                    <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>AI Confidence</span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, color: s.color }}>
                      {selected.confidence}%
                    </span>
                  </div>
                  <div style={{ height: 4, background: 'var(--border)', borderRadius: 99 }}>
                    <div style={{
                      height: '100%', width: `${selected.confidence}%`,
                      background: s.color, borderRadius: 99,
                      transition: 'width 0.5s ease',
                    }} />
                  </div>
                </div>

                {/* AI explanation */}
                <div style={{
                  padding: '12px 14px', marginBottom: 16,
                  background: 'var(--bg-subtle)',
                  border: '1px solid var(--border)',
                  borderLeft: `3px solid ${s.color}`,
                  borderRadius: 'var(--radius-sm)',
                }}>
                  <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>
                    AI Analysis
                  </div>
                  <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.7 }}>
                    {selected.aiExplanation}
                  </div>
                </div>

                {/* Actions */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {[
                    { label: 'Freeze Account',         color: 'var(--risk-critical)', bg: 'var(--risk-critical-bg)', border: 'var(--risk-critical-border)' },
                    { label: 'Escalate to Compliance', color: 'var(--risk-high)',     bg: 'var(--risk-high-bg)',     border: 'var(--risk-high-border)'     },
                    { label: 'Mark False Positive',    color: 'var(--text-secondary)',bg: 'var(--bg-subtle)',        border: 'var(--border)'               },
                  ].map(btn => (
                    <button key={btn.label} style={{
                      padding: '8px 14px', borderRadius: 'var(--radius-sm)',
                      background: btn.bg, border: `1px solid ${btn.border}`,
                      color: btn.color, fontSize: 12, fontWeight: 500,
                      textAlign: 'left', transition: 'opacity 0.13s',
                      letterSpacing: '-0.1px',
                    }}
                    onMouseEnter={e => e.currentTarget.style.opacity = '0.75'}
                    onMouseLeave={e => e.currentTarget.style.opacity = '1'}
                    >
                      {btn.label}
                    </button>
                  ))}
                </div>
              </div>
            )
          })()}
        </div>
      </div>
    </div>
  )
}
