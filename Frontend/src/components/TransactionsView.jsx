import { useState, useEffect } from 'react'
import { RECENT_TRANSACTIONS } from '../data/mockData'

const EXTRA = [
  { id:'TXN-88395', from:'ACC-7741', to:'ACC-4471', amount:280000, currency:'USD', rail:'ACH',    risk:'high',     ts:'14:28:22', status:'flagged'   },
  { id:'TXN-88392', from:'ACC-9980', to:'ACC-4471', amount:95000,  currency:'USD', rail:'Wire',   risk:'medium',   ts:'14:27:58', status:'reviewing' },
  { id:'TXN-88388', from:'BNK-3301', to:'EXC-0044', amount:75000,  currency:'USD', rail:'Wire',   risk:'critical', ts:'14:27:30', status:'flagged'   },
  { id:'TXN-88382', from:'MRC-8814', to:'ACC-1129', amount:21000,  currency:'USD', rail:'Card',   risk:'low',      ts:'14:26:55', status:'cleared'   },
  { id:'TXN-88377', from:'EXC-9017', to:'ACC-4471', amount:130000, currency:'USD', rail:'Crypto', risk:'high',     ts:'14:26:20', status:'flagged'   },
]

const ALL = [...RECENT_TRANSACTIONS, ...EXTRA]

const RISK_MAP = {
  critical: { color: 'var(--risk-critical)', bg: 'var(--risk-critical-bg)', border: 'var(--risk-critical-border)' },
  high:     { color: 'var(--risk-high)',     bg: 'var(--risk-high-bg)',     border: 'var(--risk-high-border)'     },
  medium:   { color: 'var(--risk-medium)',   bg: 'var(--risk-medium-bg)',   border: 'var(--risk-medium-border)'   },
  low:      { color: 'var(--risk-low)',      bg: 'var(--risk-low-bg)',      border: 'var(--risk-low-border)'      },
}

const STATUS_MAP = {
  flagged:   { color: 'var(--status-flagged)',   bg: 'var(--risk-critical-bg)', border: 'var(--risk-critical-border)' },
  frozen:    { color: 'var(--status-frozen)',    bg: 'var(--risk-high-bg)',     border: 'var(--risk-high-border)'     },
  reviewing: { color: 'var(--status-reviewing)', bg: 'var(--risk-medium-bg)',   border: 'var(--risk-medium-border)'   },
  cleared:   { color: 'var(--status-cleared)',   bg: 'var(--risk-low-bg)',      border: 'var(--risk-low-border)'      },
}

export default function TransactionsView() {
  const [search, setSearch]         = useState('')
  const [riskFilter, setRiskFilter] = useState('all')
  const [sortBy, setSortBy]         = useState('ts')
  const [sortDir, setSortDir]       = useState('desc')
  const [liveCount, setLiveCount]   = useState(ALL.length)

  useEffect(() => {
    const iv = setInterval(() => setLiveCount(n => n + 1), 1400)
    return () => clearInterval(iv)
  }, [])

  const filtered = ALL
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

  const counts = ALL.reduce((acc, tx) => { acc[tx.risk] = (acc[tx.risk] || 0) + 1; return acc }, {})
  const totalVolume = filtered.reduce((s, tx) => s + tx.amount, 0)

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
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'auto', background: 'var(--bg-base)' }}>

      {/* Header */}
      <div style={{
        padding: '24px 28px 16px',
        background: 'var(--bg-card)',
        borderBottom: '1px solid var(--border)',
        boxShadow: 'var(--shadow-sm)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <div>
            <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 20, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.3px', marginBottom: 3 }}>
              Transactions
            </h1>
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>{liveCount.toLocaleString()}</span> processed · streaming live
            </div>
          </div>

          {/* Search */}
          <div style={{
            padding: '7px 13px',
            background: 'var(--bg-subtle)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            display: 'flex', alignItems: 'center', gap: 8,
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
                color: 'var(--text-primary)', fontSize: 12, width: 180,
              }}
            />
          </div>
        </div>

        {/* Risk filter + total */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 11, color: 'var(--text-muted)', marginRight: 2 }}>Filter:</span>
          {[['all', 'All', ALL.length], ['critical', 'Critical', counts.critical || 0], ['high', 'High', counts.high || 0], ['medium', 'Medium', counts.medium || 0], ['low', 'Low', counts.low || 0]].map(([f, label, count]) => {
            const isActive = riskFilter === f
            const s = RISK_MAP[f]
            return (
              <button
                key={f}
                onClick={() => setRiskFilter(f)}
                style={{
                  padding: '4px 11px', borderRadius: 99, fontSize: 11,
                  background: isActive ? (s?.bg || 'var(--accent-bg)') : 'transparent',
                  border: `1px solid ${isActive ? (s?.border || 'var(--accent-border)') : 'var(--border)'}`,
                  color: isActive ? (s?.color || 'var(--accent)') : 'var(--text-muted)',
                  fontWeight: isActive ? 600 : 400,
                  display: 'flex', alignItems: 'center', gap: 5,
                  transition: 'all 0.13s',
                }}
              >
                {label}
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10 }}>{count}</span>
              </button>
            )
          })}

          <div style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-secondary)' }}>
            Total volume:{' '}
            <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--text-primary)' }}>
              ${(totalVolume / 1000000).toFixed(2)}M
            </span>
          </div>
        </div>
      </div>

      {/* Table */}
      <div style={{ padding: '20px 28px', flex: 1 }}>
        <div style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          boxShadow: 'var(--shadow-sm)',
          overflow: 'hidden',
        }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-subtle)', borderBottom: '1px solid var(--border)' }}>
                  {COLS.map(h => (
                    <th
                      key={h.col}
                      onClick={() => toggleSort(h.col)}
                      style={{
                        padding: '10px 16px', textAlign: 'left',
                        fontSize: 10, color: sortBy === h.col ? 'var(--accent)' : 'var(--text-muted)',
                        textTransform: 'uppercase', letterSpacing: '0.07em',
                        fontWeight: 600, whiteSpace: 'nowrap',
                        cursor: 'pointer', userSelect: 'none',
                        transition: 'color 0.13s',
                      }}
                    >
                      {h.label}{' '}
                      {sortBy === h.col
                        ? <span style={{ color: 'var(--accent)' }}>{sortDir === 'asc' ? '↑' : '↓'}</span>
                        : <span style={{ color: 'var(--border-strong)', fontSize: 9 }}>⇅</span>
                      }
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((tx, i) => {
                  const rs = RISK_MAP[tx.risk]
                  const ss = STATUS_MAP[tx.status]
                  return (
                    <tr
                      key={tx.id}
                      style={{
                        borderBottom: '1px solid var(--border)',
                        transition: 'background 0.1s',
                        animation: `fadeSlideIn 0.22s ease ${i * 28}ms both`,
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-hover)'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      <td style={{ padding: '11px 16px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-muted)' }}>
                        {tx.id}
                      </td>
                      <td style={{ padding: '11px 16px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-secondary)' }}>
                        {tx.from}
                      </td>
                      <td style={{ padding: '11px 16px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-secondary)' }}>
                        {tx.to}
                      </td>
                      <td style={{ padding: '11px 16px', fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>
                        ${tx.amount.toLocaleString()}
                        <span style={{ fontSize: 10, color: 'var(--text-faint)', fontWeight: 400, marginLeft: 3 }}>{tx.currency}</span>
                      </td>
                      <td style={{ padding: '11px 16px' }}>
                        <span style={{
                          fontSize: 10, fontFamily: 'var(--font-mono)',
                          background: 'var(--bg-subtle)', border: '1px solid var(--border)',
                          padding: '2px 7px', borderRadius: 3,
                          color: 'var(--text-secondary)',
                        }}>{tx.rail}</span>
                      </td>
                      <td style={{ padding: '11px 16px' }}>
                        <span style={{
                          fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
                          letterSpacing: '0.05em',
                          color: rs.color, background: rs.bg,
                          border: `1px solid ${rs.border}`,
                          padding: '2px 7px', borderRadius: 4,
                        }}>
                          {tx.risk}
                        </span>
                      </td>
                      <td style={{ padding: '11px 16px' }}>
                        <span style={{
                          fontSize: 10, fontWeight: 600,
                          color: ss.color, background: ss.bg,
                          border: `1px solid ${ss.border}`,
                          padding: '2px 8px', borderRadius: 99,
                        }}>
                          {tx.status}
                        </span>
                      </td>
                      <td style={{ padding: '11px 16px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-muted)' }}>
                        {tx.ts}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Table footer */}
          <div style={{
            padding: '10px 16px',
            borderTop: '1px solid var(--border)',
            background: 'var(--bg-subtle)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Showing{' '}
              <span style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>{filtered.length}</span>
              {' '}of {ALL.length} transactions
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
                }}>
                  {p}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
