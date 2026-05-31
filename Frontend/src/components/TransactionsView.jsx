import { useState, useEffect } from 'react'
import {
  RECENT_TRANSACTIONS,
  ACH_BATCHES,
  WIRE_INFLIGHT,
  CARD_AUTHS,
  ACH_RETURN_CODES,
} from '../data/mockData'

const EXTRA = [
  { id:'TXN-88395', from:'ACC-7741', to:'ACC-4471', amount:280000, currency:'USD', rail:'ACH',    risk:'high',     ts:'14:28:22', status:'flagged'   },
  { id:'TXN-88392', from:'ACC-9980', to:'ACC-4471', amount:95000,  currency:'USD', rail:'Wire',   risk:'medium',   ts:'14:27:58', status:'reviewing' },
  { id:'TXN-88388', from:'BNK-3301', to:'EXC-0044', amount:75000,  currency:'USD', rail:'Wire',   risk:'critical', ts:'14:27:30', status:'flagged'   },
  { id:'TXN-88382', from:'MRC-8814', to:'ACC-1129', amount:21000,  currency:'USD', rail:'Card',   risk:'low',      ts:'14:26:55', status:'cleared'   },
  { id:'TXN-88377', from:'EXC-9017', to:'ACC-4471', amount:130000, currency:'USD', rail:'Crypto', risk:'high',     ts:'14:26:20', status:'flagged'   },
]
const ALL_HISTORY = [...RECENT_TRANSACTIONS, ...EXTRA]

const RISK_MAP = {
  critical: { color: 'var(--risk-critical)', bg: 'var(--risk-critical-bg)', border: 'var(--risk-critical-border)' },
  high:     { color: 'var(--risk-high)',     bg: 'var(--risk-high-bg)',     border: 'var(--risk-high-border)'     },
  medium:   { color: 'var(--risk-medium)',   bg: 'var(--risk-medium-bg)',   border: 'var(--risk-medium-border)'   },
  low:      { color: 'var(--risk-low)',      bg: 'var(--risk-low-bg)',      border: 'var(--risk-low-border)'      },
}

const STATUS_MAP = {
  flagged:            { color: 'var(--status-flagged)',   bg: 'var(--risk-critical-bg)', border: 'var(--risk-critical-border)' },
  frozen:             { color: 'var(--status-frozen)',    bg: 'var(--risk-high-bg)',     border: 'var(--risk-high-border)'     },
  reviewing:          { color: 'var(--status-reviewing)', bg: 'var(--risk-medium-bg)',   border: 'var(--risk-medium-border)'   },
  cleared:            { color: 'var(--status-cleared)',   bg: 'var(--risk-low-bg)',      border: 'var(--risk-low-border)'      },
  processing:         { color: '#0C7A5A', bg: '#EAF5F1', border: '#B2D9CE' },
  submitted:          { color: '#4A5568', bg: '#F4F6F9', border: '#C8D0DC' },
  pending:            { color: '#4A5568', bg: '#F4F6F9', border: '#C8D0DC' },
  partially_returned: { color: '#B45309', bg: '#FEF3E7', border: '#F5CEAA' },
  returned:           { color: '#C8241A', bg: '#FEF0EF', border: '#F5C0BC' },
  authorized:         { color: '#0C7A5A', bg: '#EAF5F1', border: '#B2D9CE' },
  stale:              { color: '#B45309', bg: '#FEF3E7', border: '#F5CEAA' },
  delayed:            { color: '#92620A', bg: '#FEFAE7', border: '#EFD98A' },
}

function StatusChip({ status }) {
  const s = STATUS_MAP[status] || STATUS_MAP.pending
  const label = status.replace(/_/g, ' ')
  return (
    <span style={{
      fontSize: 10, fontWeight: 600,
      color: s.color, background: s.bg, border: `1px solid ${s.border}`,
      padding: '2px 8px', borderRadius: 99,
      textTransform: 'capitalize', whiteSpace: 'nowrap',
    }}>
      {label}
    </span>
  )
}

function RiskChip({ risk }) {
  const s = RISK_MAP[risk]
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em',
      color: s.color, background: s.bg, border: `1px solid ${s.border}`,
      padding: '2px 7px', borderRadius: 4,
    }}>
      {risk}
    </span>
  )
}

// ── Age label for wires/card auths ──────────────────────────────────────────
function AgeLabel({ minutes, warnAfter, dangerAfter }) {
  const color = minutes >= dangerAfter
    ? 'var(--risk-critical)'
    : minutes >= warnAfter
    ? 'var(--risk-high)'
    : 'var(--text-muted)'

  const display = minutes < 60
    ? `${minutes}m`
    : `${Math.floor(minutes / 60)}h ${minutes % 60}m`

  return (
    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color, fontWeight: minutes >= warnAfter ? 600 : 400 }}>
      {display}
    </span>
  )
}

// ── ACH Batch Card ───────────────────────────────────────────────────────────
function AchBatchCard({ batch, isExpanded, onToggle }) {
  const rs = RISK_MAP[batch.risk]
  const ss = STATUS_MAP[batch.status] || STATUS_MAP.submitted

  return (
    <div style={{
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      overflow: 'hidden',
      marginBottom: 10,
      background: 'var(--bg-card)',
      boxShadow: 'var(--shadow-sm)',
    }}>
      {/* Batch header row */}
      <div
        onClick={onToggle}
        style={{
          padding: '13px 16px',
          display: 'flex', alignItems: 'center', gap: 14,
          cursor: 'pointer',
          transition: 'background 0.1s',
        }}
        onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-hover)'}
        onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
      >
        {/* Expand chevron */}
        <svg
          width="14" height="14" viewBox="0 0 14 14" fill="none"
          style={{
            flexShrink: 0,
            transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
            transition: 'transform 0.18s ease',
            color: 'var(--text-muted)',
          }}
        >
          <path d="M5 3l4 4-4 4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>

        {/* Filename + meta */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>
              {batch.filename}
            </span>
            {batch.returnCount > 0 && (
              <span style={{
                fontSize: 10, fontWeight: 700,
                color: 'var(--risk-critical)',
                background: 'var(--risk-critical-bg)',
                border: '1px solid var(--risk-critical-border)',
                padding: '1px 6px', borderRadius: 99,
              }}>
                {batch.returnCount} returns
              </span>
            )}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            {batch.txnCount.toLocaleString()} transactions ·{' '}
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
              ${(batch.totalAmount / 1000000).toFixed(2)}M
            </span>
            {' '}· Submitted {batch.submittedAt}
          </div>
        </div>

        {/* Risk + status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          <RiskChip risk={batch.risk} />
          <StatusChip status={batch.status} />
        </div>
      </div>

      {/* Drill-down table */}
      {isExpanded && (
        <div style={{ borderTop: '1px solid var(--border)', animation: 'fadeIn 0.15s ease' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--bg-subtle)' }}>
                {['ID', 'From', 'To', 'Amount', 'Return Code', 'Risk', 'Status'].map(h => (
                  <th key={h} style={{
                    padding: '7px 14px', textAlign: 'left',
                    fontSize: 10, color: 'var(--text-muted)',
                    textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 600,
                    borderBottom: '1px solid var(--border)',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {batch.transactions.map((tx, i) => (
                <tr key={tx.id}
                  style={{ borderBottom: '1px solid var(--border)', transition: 'background 0.1s' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-hover)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  <td style={{ padding: '8px 14px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-muted)' }}>{tx.id}</td>
                  <td style={{ padding: '8px 14px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-secondary)' }}>{tx.from}</td>
                  <td style={{ padding: '8px 14px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-secondary)' }}>{tx.to}</td>
                  <td style={{ padding: '8px 14px', fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>
                    ${tx.amount.toLocaleString()}
                  </td>
                  <td style={{ padding: '8px 14px' }}>
                    {tx.returnCode ? (
                      <span style={{
                        fontSize: 11, fontFamily: 'var(--font-mono)',
                        color: 'var(--risk-critical)', fontWeight: 600,
                      }}>
                        {tx.returnCode}
                        <span style={{ fontFamily: 'var(--font-ui)', fontWeight: 400, color: 'var(--text-muted)', marginLeft: 5 }}>
                          {ACH_RETURN_CODES[tx.returnCode]}
                        </span>
                      </span>
                    ) : (
                      <span style={{ color: 'var(--text-faint)', fontSize: 11 }}>—</span>
                    )}
                  </td>
                  <td style={{ padding: '8px 14px' }}><RiskChip risk={tx.risk} /></td>
                  <td style={{ padding: '8px 14px' }}><StatusChip status={tx.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ padding: '8px 14px', background: 'var(--bg-subtle)', borderTop: '1px solid var(--border)' }}>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Showing {batch.transactions.length} of {batch.txnCount} transactions in this batch
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

// ── In-Flight Tab ────────────────────────────────────────────────────────────
function InFlightTab() {
  const [expandedBatch, setExpandedBatch] = useState(null)

  const totalPending =
    ACH_BATCHES.reduce((s, b) => s + b.totalAmount, 0) +
    WIRE_INFLIGHT.reduce((s, w) => s + w.amount, 0) +
    CARD_AUTHS.reduce((s, c) => s + c.amount, 0)

  return (
    <div style={{ padding: '20px 28px' }}>

      {/* Summary strip */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 24,
      }}>
        {[
          { label: 'ACH Batches',        value: `${ACH_BATCHES.length} batches`, sub: `${ACH_BATCHES.reduce((s,b)=>s+b.txnCount,0).toLocaleString()} transactions` },
          { label: 'Wire Transactions',  value: `${WIRE_INFLIGHT.length} pending`, sub: `$${(WIRE_INFLIGHT.reduce((s,w)=>s+w.amount,0)/1000000).toFixed(2)}M in-flight` },
          { label: 'Card Authorizations',value: `${CARD_AUTHS.length} open`,      sub: `${CARD_AUTHS.filter(c=>c.status==='stale').length} stale` },
        ].map(s => (
          <div key={s.label} style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius)', padding: '14px 16px',
            boxShadow: 'var(--shadow-sm)',
          }}>
            <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--text-muted)', fontWeight: 600, marginBottom: 6 }}>
              {s.label}
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 3 }}>
              {s.value}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{s.sub}</div>
          </div>
        ))}
      </div>

      {/* ACH Section */}
      <div style={{ marginBottom: 28 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
          <div style={{
            fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.09em',
            color: 'var(--text-secondary)',
          }}>
            ACH Batches
          </div>
          <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            {ACH_BATCHES.length} files
          </span>
        </div>
        {ACH_BATCHES.map(batch => (
          <AchBatchCard
            key={batch.id}
            batch={batch}
            isExpanded={expandedBatch === batch.id}
            onToggle={() => setExpandedBatch(prev => prev === batch.id ? null : batch.id)}
          />
        ))}
      </div>

      {/* Wire Section */}
      <div style={{ marginBottom: 28 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.09em', color: 'var(--text-secondary)' }}>
            Wire Transactions
          </div>
          <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            {WIRE_INFLIGHT.length} pending
          </span>
        </div>
        <div style={{
          background: 'var(--bg-card)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)', overflow: 'hidden', boxShadow: 'var(--shadow-sm)',
        }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--bg-subtle)', borderBottom: '1px solid var(--border)' }}>
                {['Transaction', 'From', 'To', 'Amount', 'SWIFT', 'Submitted', 'Age', 'Risk', 'Status'].map(h => (
                  <th key={h} style={{
                    padding: '9px 14px', textAlign: 'left',
                    fontSize: 10, color: 'var(--text-muted)',
                    textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 600, whiteSpace: 'nowrap',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {WIRE_INFLIGHT.map((w, i) => (
                <tr key={w.id}
                  style={{ borderBottom: '1px solid var(--border)', transition: 'background 0.1s', animation: `fadeSlideIn 0.22s ease ${i * 30}ms both` }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-hover)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-muted)' }}>{w.id}</td>
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-secondary)' }}>{w.from}</td>
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-secondary)' }}>{w.to}</td>
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>
                    ${w.amount.toLocaleString()}
                  </td>
                  <td style={{ padding: '10px 14px' }}>
                    <span style={{
                      fontSize: 10, fontFamily: 'var(--font-mono)',
                      background: 'var(--bg-subtle)', border: '1px solid var(--border)',
                      padding: '2px 6px', borderRadius: 3, color: 'var(--text-secondary)',
                    }}>{w.swift}</span>
                  </td>
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>{w.submittedAt}</td>
                  <td style={{ padding: '10px 14px' }}>
                    <AgeLabel minutes={w.ageMin} warnAfter={30} dangerAfter={90} />
                  </td>
                  <td style={{ padding: '10px 14px' }}><RiskChip risk={w.risk} /></td>
                  <td style={{ padding: '10px 14px' }}><StatusChip status={w.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Card Authorizations Section */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.09em', color: 'var(--text-secondary)' }}>
            Card Authorizations
          </div>
          <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
          {CARD_AUTHS.some(c => c.status === 'stale') && (
            <span style={{
              fontSize: 10, fontWeight: 700,
              color: 'var(--risk-high)', background: 'var(--risk-high-bg)',
              border: '1px solid var(--risk-high-border)',
              padding: '1px 7px', borderRadius: 99,
            }}>
              {CARD_AUTHS.filter(c => c.status === 'stale').length} stale
            </span>
          )}
          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            {CARD_AUTHS.length} open
          </span>
        </div>
        <div style={{
          background: 'var(--bg-card)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)', overflow: 'hidden', boxShadow: 'var(--shadow-sm)',
        }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--bg-subtle)', borderBottom: '1px solid var(--border)' }}>
                {['Auth ID', 'Merchant', 'Account', 'Amount', 'Network', 'Age', 'Risk', 'Status'].map(h => (
                  <th key={h} style={{
                    padding: '9px 14px', textAlign: 'left',
                    fontSize: 10, color: 'var(--text-muted)',
                    textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 600, whiteSpace: 'nowrap',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {CARD_AUTHS.map((c, i) => (
                <tr key={c.id}
                  style={{
                    borderBottom: '1px solid var(--border)', transition: 'background 0.1s',
                    animation: `fadeSlideIn 0.22s ease ${i * 30}ms both`,
                    background: c.status === 'stale' ? 'rgba(180,83,9,0.03)' : 'transparent',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-hover)'}
                  onMouseLeave={e => e.currentTarget.style.background = c.status === 'stale' ? 'rgba(180,83,9,0.03)' : 'transparent'}
                >
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-muted)' }}>{c.id}</td>
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-secondary)' }}>{c.merchant}</td>
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-secondary)' }}>{c.account}</td>
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>
                    ${c.amount.toLocaleString()}
                  </td>
                  <td style={{ padding: '10px 14px' }}>
                    <span style={{
                      fontSize: 10, fontFamily: 'var(--font-mono)',
                      background: 'var(--bg-subtle)', border: '1px solid var(--border)',
                      padding: '2px 6px', borderRadius: 3, color: 'var(--text-secondary)',
                    }}>{c.network}</span>
                  </td>
                  <td style={{ padding: '10px 14px' }}>
                    <AgeLabel minutes={c.ageMin} warnAfter={60} dangerAfter={200} />
                  </td>
                  <td style={{ padding: '10px 14px' }}><RiskChip risk={c.risk} /></td>
                  <td style={{ padding: '10px 14px' }}><StatusChip status={c.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── History Tab ──────────────────────────────────────────────────────────────
function HistoryTab() {
  const [search, setSearch]         = useState('')
  const [riskFilter, setRiskFilter] = useState('all')
  const [sortBy, setSortBy]         = useState('ts')
  const [sortDir, setSortDir]       = useState('desc')

  const filtered = ALL_HISTORY
    .filter(tx => {
      if (riskFilter !== 'all' && tx.risk !== riskFilter) return false
      if (search) {
        const s = search.toLowerCase()
        return tx.id.toLowerCase().includes(s) || tx.from.toLowerCase().includes(s) || tx.to.toLowerCase().includes(s)
      }
      return true
    })
    .sort((a, b) => {
      const av = sortBy === 'amount' ? +a[sortBy] : a[sortBy]
      const bv = sortBy === 'amount' ? +b[sortBy] : b[sortBy]
      return sortDir === 'asc' ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1)
    })

  const toggleSort = col => {
    if (sortBy === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortBy(col); setSortDir('desc') }
  }

  const counts = ALL_HISTORY.reduce((acc, tx) => { acc[tx.risk] = (acc[tx.risk] || 0) + 1; return acc }, {})

  const COLS = [
    { label: 'Transaction', col: 'id'     },
    { label: 'From',        col: 'from'   },
    { label: 'To',          col: 'to'     },
    { label: 'Amount',      col: 'amount' },
    { label: 'Rail',        col: 'rail'   },
    { label: 'Risk',        col: 'risk'   },
    { label: 'Status',      col: 'status' },
    { label: 'Time',        col: 'ts'     },
  ]

  return (
    <div style={{ padding: '20px 28px' }}>

      {/* Filters row */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16,
        flexWrap: 'wrap',
      }}>
        {/* Search */}
        <div style={{
          padding: '7px 12px', background: 'var(--bg-card)',
          border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
          display: 'flex', alignItems: 'center', gap: 7,
          boxShadow: 'var(--shadow-sm)',
        }}>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <circle cx="5" cy="5" r="3.5" stroke="var(--text-muted)" strokeWidth="1.3"/>
            <line x1="7.5" y1="7.5" x2="10.5" y2="10.5" stroke="var(--text-muted)" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search ID, account..."
            style={{
              background: 'transparent', border: 'none', outline: 'none',
              color: 'var(--text-primary)', fontSize: 12, width: 170,
            }}
          />
        </div>

        {/* Risk filter chips */}
        {[['all','All',ALL_HISTORY.length],['critical','Critical',counts.critical||0],['high','High',counts.high||0],['medium','Medium',counts.medium||0],['low','Low',counts.low||0]].map(([f, label, count]) => {
          const isActive = riskFilter === f
          const s = RISK_MAP[f]
          return (
            <button key={f} onClick={() => setRiskFilter(f)} style={{
              padding: '4px 11px', borderRadius: 99, fontSize: 11,
              background: isActive ? (s?.bg || 'var(--accent-bg)') : 'transparent',
              border: `1px solid ${isActive ? (s?.border || 'var(--accent-border)') : 'var(--border)'}`,
              color: isActive ? (s?.color || 'var(--accent)') : 'var(--text-muted)',
              fontWeight: isActive ? 600 : 400, display: 'flex', alignItems: 'center', gap: 5,
              transition: 'all 0.13s',
            }}>
              {label}
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10 }}>{count}</span>
            </button>
          )
        })}

        <div style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-secondary)' }}>
          Total:{' '}
          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--text-primary)' }}>
            ${(filtered.reduce((s, tx) => s + tx.amount, 0) / 1000000).toFixed(2)}M
          </span>
        </div>
      </div>

      <div style={{
        background: 'var(--bg-card)', border: '1px solid var(--border)',
        borderRadius: 'var(--radius)', overflow: 'hidden', boxShadow: 'var(--shadow-sm)',
      }}>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--bg-subtle)', borderBottom: '1px solid var(--border)' }}>
                {COLS.map(h => (
                  <th key={h.col} onClick={() => toggleSort(h.col)} style={{
                    padding: '10px 14px', textAlign: 'left',
                    fontSize: 10, color: sortBy === h.col ? 'var(--accent)' : 'var(--text-muted)',
                    textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 600,
                    whiteSpace: 'nowrap', cursor: 'pointer', userSelect: 'none',
                    transition: 'color 0.13s',
                  }}>
                    {h.label}{' '}
                    {sortBy === h.col
                      ? <span style={{ color: 'var(--accent)' }}>{sortDir === 'asc' ? '↑' : '↓'}</span>
                      : <span style={{ color: 'var(--border-strong)', fontSize: 9 }}>⇅</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((tx, i) => {
                const ss = STATUS_MAP[tx.status] || STATUS_MAP.pending
                return (
                  <tr key={tx.id}
                    style={{ borderBottom: '1px solid var(--border)', transition: 'background 0.1s', animation: `fadeSlideIn 0.22s ease ${i * 25}ms both` }}
                    onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-hover)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                  >
                    <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-muted)' }}>{tx.id}</td>
                    <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-secondary)' }}>{tx.from}</td>
                    <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-secondary)' }}>{tx.to}</td>
                    <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>
                      ${tx.amount.toLocaleString()}
                      <span style={{ fontSize: 10, color: 'var(--text-faint)', fontWeight: 400, marginLeft: 3 }}>{tx.currency}</span>
                    </td>
                    <td style={{ padding: '10px 14px' }}>
                      <span style={{
                        fontSize: 10, fontFamily: 'var(--font-mono)',
                        background: 'var(--bg-subtle)', border: '1px solid var(--border)',
                        padding: '2px 7px', borderRadius: 3, color: 'var(--text-secondary)',
                      }}>{tx.rail}</span>
                    </td>
                    <td style={{ padding: '10px 14px' }}><RiskChip risk={tx.risk} /></td>
                    <td style={{ padding: '10px 14px' }}><StatusChip status={tx.status} /></td>
                    <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-muted)' }}>{tx.ts}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        <div style={{
          padding: '10px 14px', borderTop: '1px solid var(--border)',
          background: 'var(--bg-subtle)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            Showing{' '}
            <span style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>{filtered.length}</span>
            {' '}of {ALL_HISTORY.length} settled transactions
          </span>
          <div style={{ display: 'flex', gap: 4 }}>
            {['←', '1', '2', '3', '→'].map(p => (
              <button key={p} style={{
                width: 26, height: 26, borderRadius: 'var(--radius-sm)',
                background: p === '1' ? 'var(--accent-bg)' : 'var(--bg-card)',
                border: `1px solid ${p === '1' ? 'var(--accent-border)' : 'var(--border)'}`,
                color: p === '1' ? 'var(--accent)' : 'var(--text-muted)',
                fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontWeight: p === '1' ? 600 : 400,
              }}>{p}</button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Main View ────────────────────────────────────────────────────────────────
export default function TransactionsView() {
  const [activeTab, setActiveTab] = useState('inflight')
  const [liveCount, setLiveCount] = useState(88421)

  useEffect(() => {
    const iv = setInterval(() => setLiveCount(n => n + 1), 1400)
    return () => clearInterval(iv)
  }, [])

  const staleCardCount = CARD_AUTHS.filter(c => c.status === 'stale').length
  const returnCount    = ACH_BATCHES.reduce((s, b) => s + b.returnCount, 0)

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'auto', background: 'var(--bg-base)' }}>

      {/* Header */}
      <div style={{
        padding: '24px 28px 0',
        background: 'var(--bg-card)',
        borderBottom: '1px solid var(--border)',
        boxShadow: 'var(--shadow-sm)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div>
            <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 20, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.3px', marginBottom: 3 }}>
              Transactions
            </h1>
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>{liveCount.toLocaleString()}</span> total processed
            </div>
          </div>

          {/* Alert badges */}
          <div style={{ display: 'flex', gap: 8 }}>
            {staleCardCount > 0 && (
              <div style={{
                padding: '6px 12px', borderRadius: 'var(--radius-sm)',
                background: 'var(--risk-high-bg)', border: '1px solid var(--risk-high-border)',
                fontSize: 11, color: 'var(--risk-high)', fontWeight: 500,
                display: 'flex', alignItems: 'center', gap: 6,
              }}>
                <div style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--risk-high)', animation: 'subtlePulse 2s ease-in-out infinite' }} />
                {staleCardCount} stale card auth{staleCardCount > 1 ? 's' : ''}
              </div>
            )}
            {returnCount > 0 && (
              <div style={{
                padding: '6px 12px', borderRadius: 'var(--radius-sm)',
                background: 'var(--risk-critical-bg)', border: '1px solid var(--risk-critical-border)',
                fontSize: 11, color: 'var(--risk-critical)', fontWeight: 500,
                display: 'flex', alignItems: 'center', gap: 6,
              }}>
                <div style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--risk-critical)' }} />
                {returnCount} ACH return{returnCount > 1 ? 's' : ''}
              </div>
            )}
          </div>
        </div>

        {/* Tab bar */}
        <div style={{ display: 'flex', gap: 0 }}>
          {[
            { id: 'inflight', label: 'In-Flight', badge: ACH_BATCHES.length + WIRE_INFLIGHT.length + CARD_AUTHS.length },
            { id: 'history',  label: 'History',   badge: ALL_HISTORY.length },
          ].map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              style={{
                padding: '9px 16px',
                fontSize: 13, fontWeight: activeTab === tab.id ? 600 : 400,
                color: activeTab === tab.id ? 'var(--accent)' : 'var(--text-muted)',
                background: 'transparent', border: 'none',
                borderBottom: activeTab === tab.id ? '2px solid var(--accent)' : '2px solid transparent',
                display: 'flex', alignItems: 'center', gap: 7,
                transition: 'all 0.13s',
                marginBottom: -1,
                letterSpacing: '-0.1px',
              }}
            >
              {tab.label}
              <span style={{
                fontSize: 10, fontFamily: 'var(--font-mono)',
                background: activeTab === tab.id ? 'var(--accent-bg)' : 'var(--bg-subtle)',
                color: activeTab === tab.id ? 'var(--accent)' : 'var(--text-faint)',
                border: `1px solid ${activeTab === tab.id ? 'var(--accent-border)' : 'var(--border)'}`,
                padding: '1px 5px', borderRadius: 99,
              }}>
                {tab.badge}
              </span>
            </button>
          ))}
        </div>
      </div>

      {activeTab === 'inflight' ? <InFlightTab /> : <HistoryTab />}
    </div>
  )
}
