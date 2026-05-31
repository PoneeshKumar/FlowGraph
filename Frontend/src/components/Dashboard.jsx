import { useState, useEffect, useRef } from 'react'
import { METRICS, RECENT_ALERTS, RECENT_TRANSACTIONS } from '../data/mockData'

function MetricCard({ label, value, format, delta, accentColor, delay }) {
  const [displayed, setDisplayed] = useState(0)

  useEffect(() => {
    let start = null
    const duration = 1000
    const step = (ts) => {
      if (!start) start = ts
      const p = Math.min((ts - start) / duration, 1)
      const ease = 1 - Math.pow(1 - p, 3)
      setDisplayed(Math.round(ease * value))
      if (p < 1) requestAnimationFrame(step)
    }
    const timer = setTimeout(() => requestAnimationFrame(step), delay)
    return () => clearTimeout(timer)
  }, [value, delay])

  const formatted = format === 'currency'
    ? `$${(displayed / 1000000).toFixed(2)}M`
    : displayed.toLocaleString()

  const isUp = delta > 0
  const isBad = (label === 'Cycles Detected' || label === 'Risk Alerts') ? isUp : !isUp

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: '20px 22px',
      boxShadow: 'var(--shadow-sm)',
      animation: `fadeSlideIn 0.35s ease ${delay}ms both`,
    }}>
      <div style={{
        fontSize: 11,
        color: 'var(--text-muted)',
        textTransform: 'uppercase',
        letterSpacing: '0.07em',
        fontWeight: 600,
        marginBottom: 10,
      }}>
        {label}
      </div>
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 26,
        fontWeight: 700,
        color: accentColor,
        letterSpacing: '-0.5px',
        lineHeight: 1,
        marginBottom: 10,
      }}>
        {formatted}
      </div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 4,
        fontSize: 11,
        color: isBad ? 'var(--risk-critical)' : 'var(--accent)',
      }}>
        <span>{isUp ? '↑' : '↓'}</span>
        <span style={{ fontWeight: 600 }}>{Math.abs(delta)}%</span>
        <span style={{ color: 'var(--text-faint)' }}>vs yesterday</span>
      </div>
    </div>
  )
}

function RiskChip({ severity }) {
  const map = {
    critical: { color: 'var(--risk-critical)', bg: 'var(--risk-critical-bg)', border: 'var(--risk-critical-border)' },
    high:     { color: 'var(--risk-high)',     bg: 'var(--risk-high-bg)',     border: 'var(--risk-high-border)'     },
    medium:   { color: 'var(--risk-medium)',   bg: 'var(--risk-medium-bg)',   border: 'var(--risk-medium-border)'   },
    low:      { color: 'var(--risk-low)',      bg: 'var(--risk-low-bg)',      border: 'var(--risk-low-border)'      },
  }
  const s = map[severity]
  return (
    <span style={{
      fontSize: 10, fontWeight: 700,
      textTransform: 'uppercase', letterSpacing: '0.06em',
      color: s.color, background: s.bg, border: `1px solid ${s.border}`,
      padding: '2px 7px', borderRadius: 4,
    }}>
      {severity}
    </span>
  )
}

function StatusChip({ status }) {
  const map = {
    flagged:   { color: 'var(--status-flagged)',   bg: 'var(--risk-critical-bg)',  border: 'var(--risk-critical-border)' },
    frozen:    { color: 'var(--status-frozen)',    bg: 'var(--risk-high-bg)',      border: 'var(--risk-high-border)'     },
    reviewing: { color: 'var(--status-reviewing)', bg: 'var(--risk-medium-bg)',    border: 'var(--risk-medium-border)'   },
    cleared:   { color: 'var(--status-cleared)',   bg: 'var(--risk-low-bg)',       border: 'var(--risk-low-border)'      },
  }
  const s = map[status]
  return (
    <span style={{
      fontSize: 10, fontWeight: 600,
      color: s.color, background: s.bg, border: `1px solid ${s.border}`,
      padding: '2px 8px', borderRadius: 99,
    }}>
      {status}
    </span>
  )
}

function AlertRow({ alert, isSelected, onSelect }) {
  const map = {
    critical: { color: 'var(--risk-critical)', dot: '#C8241A' },
    high:     { color: 'var(--risk-high)',     dot: '#B45309' },
    medium:   { color: 'var(--risk-medium)',   dot: '#92620A' },
    low:      { color: 'var(--risk-low)',      dot: '#0C7A5A' },
  }
  const s = map[alert.severity]

  return (
    <div
      onClick={() => onSelect(alert)}
      style={{
        padding: '11px 14px',
        borderRadius: 'var(--radius-sm)',
        border: `1px solid ${isSelected ? s.dot + '40' : 'var(--border)'}`,
        background: isSelected ? `${s.dot}08` : 'transparent',
        cursor: 'pointer',
        transition: 'all 0.13s ease',
        marginBottom: 5,
      }}
      onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = 'var(--bg-hover)' }}
      onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = 'transparent' }}
    >
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
        <div style={{
          width: 5, height: 5, borderRadius: '50%',
          background: s.dot,
          marginTop: 6, flexShrink: 0,
        }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 3 }}>
            <span style={{ fontSize: 11, fontWeight: 600, color: s.color }}>
              {alert.type}
            </span>
            <span style={{ fontSize: 10, color: 'var(--text-faint)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>
              {alert.timestamp}
            </span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.45 }}>
            {alert.message}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
            <span style={{
              fontSize: 10, fontFamily: 'var(--font-mono)',
              color: 'var(--text-muted)',
              background: 'var(--bg-subtle)',
              border: '1px solid var(--border)',
              padding: '1px 6px', borderRadius: 3,
            }}>
              ${(alert.amount / 1000).toFixed(0)}K
            </span>
            <span style={{ fontSize: 10, color: 'var(--text-faint)' }}>
              {alert.confidence}% confidence
            </span>
          </div>
        </div>
      </div>

      {isSelected && (
        <div style={{
          marginTop: 10, marginLeft: 15,
          padding: '10px 12px',
          background: 'var(--bg-subtle)',
          border: '1px solid var(--border)',
          borderLeft: `3px solid ${s.dot}`,
          borderRadius: 'var(--radius-sm)',
          animation: 'fadeSlideIn 0.18s ease',
        }}>
          <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 5 }}>
            AI Analysis
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.65 }}>
            {alert.aiExplanation}
          </div>
        </div>
      )}
    </div>
  )
}

export default function Dashboard({ onNav }) {
  const [selectedAlert, setSelectedAlert] = useState(null)
  const [liveCount, setLiveCount] = useState(88421)

  useEffect(() => {
    const iv = setInterval(() => setLiveCount(n => n + Math.floor(Math.random() * 3 + 1)), 900)
    return () => clearInterval(iv)
  }, [])

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: '100vh', overflow: 'auto', background: 'var(--bg-base)' }}>

      {/* Page header */}
      <div style={{
        padding: '24px 28px 20px',
        background: 'var(--bg-card)',
        borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div>
          <h1 style={{
            fontFamily: 'var(--font-display)',
            fontSize: 20, fontWeight: 700,
            color: 'var(--text-primary)',
            letterSpacing: '-0.3px',
            marginBottom: 3,
          }}>
            Network Overview
          </h1>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            Real-time graph intelligence ·{' '}
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
              {liveCount.toLocaleString()}
            </span>{' '}
            transactions processed
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{
            padding: '7px 13px',
            background: 'var(--bg-subtle)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            fontSize: 12, color: 'var(--text-muted)',
            display: 'flex', alignItems: 'center', gap: 7,
          }}>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <circle cx="5" cy="5" r="3.5" stroke="currentColor" strokeWidth="1.3"/>
              <line x1="7.5" y1="7.5" x2="10.5" y2="10.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
            </svg>
            Search accounts, transactions...
          </div>
          <button
            onClick={() => onNav('graph')}
            style={{
              padding: '7px 14px',
              background: 'var(--accent)',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              color: 'white',
              fontSize: 12, fontWeight: 600,
              display: 'flex', alignItems: 'center', gap: 6,
              transition: 'background 0.15s',
              letterSpacing: '-0.1px',
            }}
            onMouseEnter={e => e.currentTarget.style.background = 'var(--accent-mid)'}
            onMouseLeave={e => e.currentTarget.style.background = 'var(--accent)'}
          >
            Open Graph
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
              <path d="M2 5h6M6 3l2 2-2 2" stroke="white" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
        </div>
      </div>

      <div style={{ padding: '22px 28px', flex: 1 }}>

        {/* Metric cards */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginBottom: 22 }}>
          <MetricCard label="Volume 24h"       value={METRICS.volume24h.value}      format="currency" delta={METRICS.volume24h.delta}      accentColor="var(--accent)"         delay={0}   />
          <MetricCard label="Active Accounts"  value={METRICS.activeAccounts.value} format="count"    delta={METRICS.activeAccounts.delta} accentColor="var(--text-primary)"    delay={60}  />
          <MetricCard label="Cycles Detected"  value={METRICS.cyclesDetected.value} format="count"    delta={METRICS.cyclesDetected.delta} accentColor="var(--risk-critical)"   delay={120} />
          <MetricCard label="Risk Alerts"      value={METRICS.riskAlerts.value}     format="count"    delta={METRICS.riskAlerts.delta}     accentColor="var(--risk-high)"       delay={180} />
        </div>

        {/* Main content */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.6fr', gap: 16 }}>

          {/* Alerts panel */}
          <div style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
            boxShadow: 'var(--shadow-sm)',
            display: 'flex', flexDirection: 'column',
            overflow: 'hidden',
          }}>
            <div style={{
              padding: '14px 16px',
              borderBottom: '1px solid var(--border)',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontFamily: 'var(--font-display)', fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
                  Risk Alerts
                </span>
                <span style={{
                  fontSize: 10, fontFamily: 'var(--font-mono)',
                  background: 'var(--risk-critical-bg)',
                  color: 'var(--risk-critical)',
                  border: '1px solid var(--risk-critical-border)',
                  padding: '1px 6px', borderRadius: 99, fontWeight: 700,
                }}>17</span>
              </div>
              <button
                onClick={() => onNav('alerts')}
                style={{ fontSize: 11, color: 'var(--accent)', fontWeight: 500, background: 'none', border: 'none', cursor: 'pointer' }}
              >
                View all →
              </button>
            </div>
            <div style={{ padding: '10px', overflow: 'auto', flex: 1, maxHeight: 440 }}>
              {RECENT_ALERTS.map(alert => (
                <AlertRow
                  key={alert.id}
                  alert={alert}
                  isSelected={selectedAlert?.id === alert.id}
                  onSelect={a => setSelectedAlert(prev => prev?.id === a.id ? null : a)}
                />
              ))}
            </div>
          </div>

          {/* Transactions table */}
          <div style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
            boxShadow: 'var(--shadow-sm)',
            display: 'flex', flexDirection: 'column',
            overflow: 'hidden',
          }}>
            <div style={{
              padding: '14px 16px',
              borderBottom: '1px solid var(--border)',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontFamily: 'var(--font-display)', fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
                  Recent Transactions
                </span>
                <div style={{
                  width: 6, height: 6, borderRadius: '50%',
                  background: 'var(--accent)',
                  animation: 'subtlePulse 2s ease-in-out infinite',
                }} />
              </div>
              <div style={{ display: 'flex', gap: 5 }}>
                {['All', 'Flagged', 'Cleared'].map((f, i) => (
                  <button key={f} style={{
                    fontSize: 11, padding: '3px 10px',
                    borderRadius: 99,
                    background: i === 0 ? 'var(--accent-bg)' : 'transparent',
                    border: `1px solid ${i === 0 ? 'var(--accent-border)' : 'var(--border)'}`,
                    color: i === 0 ? 'var(--accent)' : 'var(--text-muted)',
                    fontWeight: i === 0 ? 600 : 400,
                    transition: 'all 0.13s',
                  }}>
                    {f}
                  </button>
                ))}
              </div>
            </div>

            <div style={{ overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ background: 'var(--bg-subtle)' }}>
                    {['Transaction', 'Route', 'Amount', 'Rail', 'Risk', 'Status', 'Time'].map(h => (
                      <th key={h} style={{
                        padding: '9px 14px', textAlign: 'left',
                        fontSize: 10, color: 'var(--text-muted)',
                        textTransform: 'uppercase', letterSpacing: '0.07em',
                        fontWeight: 600, whiteSpace: 'nowrap',
                        borderBottom: '1px solid var(--border)',
                      }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {RECENT_TRANSACTIONS.map((tx, i) => (
                    <tr key={tx.id} style={{
                      borderBottom: '1px solid var(--border)',
                      transition: 'background 0.1s',
                      animation: `fadeSlideIn 0.25s ease ${i * 35}ms both`,
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-hover)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-muted)' }}>
                        {tx.id}
                      </td>
                      <td style={{ padding: '10px 14px', fontSize: 12 }}>
                        <span style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>{tx.from}</span>
                        <span style={{ color: 'var(--text-faint)', margin: '0 5px' }}>→</span>
                        <span style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>{tx.to}</span>
                      </td>
                      <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>
                        ${tx.amount.toLocaleString()}
                      </td>
                      <td style={{ padding: '10px 14px' }}>
                        <span style={{
                          fontSize: 10, fontFamily: 'var(--font-mono)',
                          background: 'var(--bg-subtle)', border: '1px solid var(--border)',
                          padding: '2px 6px', borderRadius: 3,
                          color: 'var(--text-secondary)',
                        }}>{tx.rail}</span>
                      </td>
                      <td style={{ padding: '10px 14px' }}>
                        <RiskChip severity={tx.risk} />
                      </td>
                      <td style={{ padding: '10px 14px' }}>
                        <StatusChip status={tx.status} />
                      </td>
                      <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-muted)' }}>
                        {tx.ts}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
