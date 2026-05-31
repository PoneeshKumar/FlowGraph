import { useState } from 'react'

const NAV = [
  { id: 'dashboard',    label: 'Dashboard',    icon: GridIcon },
  { id: 'graph',        label: 'Graph',         icon: GraphIcon },
  { id: 'alerts',       label: 'Alerts',        icon: AlertIcon, badge: 17 },
  { id: 'transactions', label: 'Transactions',  icon: TxnIcon },
]

function GridIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
      <rect x="1" y="1" width="5.5" height="5.5" rx="1.2" fill="currentColor" opacity=".85"/>
      <rect x="8.5" y="1" width="5.5" height="5.5" rx="1.2" fill="currentColor" opacity=".85"/>
      <rect x="1" y="8.5" width="5.5" height="5.5" rx="1.2" fill="currentColor" opacity=".85"/>
      <rect x="8.5" y="8.5" width="5.5" height="5.5" rx="1.2" fill="currentColor" opacity=".85"/>
    </svg>
  )
}
function GraphIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
      <circle cx="7.5" cy="7.5" r="2.2" fill="currentColor"/>
      <circle cx="2"   cy="3.5" r="1.5" fill="currentColor" opacity=".65"/>
      <circle cx="13"  cy="3.5" r="1.5" fill="currentColor" opacity=".65"/>
      <circle cx="2"   cy="11.5"r="1.5" fill="currentColor" opacity=".65"/>
      <circle cx="13"  cy="11.5"r="1.5" fill="currentColor" opacity=".65"/>
      <line x1="3.4"  y1="4.5"  x2="5.9"  y2="6.6"  stroke="currentColor" strokeWidth="1" opacity=".45"/>
      <line x1="11.6" y1="4.5"  x2="9.1"  y2="6.6"  stroke="currentColor" strokeWidth="1" opacity=".45"/>
      <line x1="3.4"  y1="10.5" x2="5.9"  y2="8.4"  stroke="currentColor" strokeWidth="1" opacity=".45"/>
      <line x1="11.6" y1="10.5" x2="9.1"  y2="8.4"  stroke="currentColor" strokeWidth="1" opacity=".45"/>
    </svg>
  )
}
function AlertIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
      <path d="M7.5 1.5L13.5 12H1.5L7.5 1.5Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
      <line x1="7.5" y1="5.5" x2="7.5" y2="8.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
      <circle cx="7.5" cy="10.5" r="0.75" fill="currentColor"/>
    </svg>
  )
}
function TxnIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
      <line x1="2" y1="7.5" x2="13" y2="7.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      <polyline points="9.5,4.5 13,7.5 9.5,10.5" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" strokeLinecap="round" fill="none"/>
      <line x1="2" y1="4.5" x2="6.5" y2="4.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" opacity=".45"/>
      <line x1="2" y1="10.5" x2="6.5" y2="10.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" opacity=".45"/>
    </svg>
  )
}

function SunIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
      <circle cx="6.5" cy="6.5" r="2.5" fill="currentColor"/>
      <line x1="6.5" y1="0.5" x2="6.5" y2="2.5"   stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      <line x1="6.5" y1="10.5" x2="6.5" y2="12.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      <line x1="0.5" y1="6.5" x2="2.5" y2="6.5"   stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      <line x1="10.5" y1="6.5" x2="12.5" y2="6.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      <line x1="2.3" y1="2.3" x2="3.7" y2="3.7"   stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      <line x1="9.3" y1="9.3" x2="10.7" y2="10.7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      <line x1="9.3" y1="2.3" x2="10.7" y2="3.7"  stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      <line x1="2.3" y1="9.3" x2="3.7" y2="10.7"  stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
    </svg>
  )
}
function MoonIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
      <path d="M10.5 7.5A5 5 0 015.5 2.5a5 5 0 100 8 5 5 0 005-3z" fill="currentColor"/>
    </svg>
  )
}

export default function Sidebar({ active, onNav, theme, onToggleTheme }) {
  const [hoverId, setHoverId] = useState(null)

  return (
    <aside style={{
      width: 'var(--sidebar-w)',
      minWidth: 'var(--sidebar-w)',
      background: 'var(--sidebar-bg)',
      borderRight: '1px solid var(--sidebar-border)',
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      position: 'sticky',
      top: 0,
      zIndex: 10,
    }}>

      {/* Logo */}
      <div style={{
        padding: '22px 20px 18px',
        borderBottom: '1px solid var(--sidebar-border)',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
      }}>
        <div style={{
          width: 28, height: 28,
          background: 'var(--accent)',
          borderRadius: 7,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexShrink: 0,
        }}>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <circle cx="7" cy="7" r="2" fill="white"/>
            <line x1="7" y1="1" x2="7" y2="4.5" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
            <line x1="7" y1="9.5" x2="7" y2="13" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
            <line x1="1" y1="7" x2="4.5" y2="7" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
            <line x1="9.5" y1="7" x2="13" y2="7" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </div>
        <div>
          <div style={{
            fontFamily: 'var(--font-display)',
            fontWeight: 700,
            fontSize: 15,
            letterSpacing: '-0.2px',
            color: 'rgba(255,255,255,0.92)',
          }}>
            FlowGraph
          </div>
          <div style={{
            fontSize: 10,
            color: 'rgba(255,255,255,0.30)',
            letterSpacing: '0.05em',
            textTransform: 'uppercase',
            marginTop: 1,
          }}>
            Intelligence Engine
          </div>
        </div>
      </div>

      {/* Section label */}
      <div style={{
        padding: '18px 20px 6px',
        fontSize: 10,
        letterSpacing: '0.09em',
        textTransform: 'uppercase',
        color: 'rgba(255,255,255,0.22)',
        fontWeight: 600,
      }}>
        Navigation
      </div>

      {/* Nav items */}
      <nav style={{ padding: '2px 10px', flex: 1 }}>
        {NAV.map(item => {
          const isActive = active === item.id
          const isHover = hoverId === item.id && !isActive
          const Icon = item.icon
          return (
            <button
              key={item.id}
              onClick={() => onNav(item.id)}
              onMouseEnter={() => setHoverId(item.id)}
              onMouseLeave={() => setHoverId(null)}
              style={{
                width: '100%',
                display: 'flex', alignItems: 'center', gap: 9,
                padding: '8px 10px',
                borderRadius: 'var(--radius-sm)',
                color: isActive
                  ? 'var(--sidebar-accent)'
                  : isHover
                  ? 'rgba(255,255,255,0.80)'
                  : 'var(--sidebar-text)',
                background: isActive
                  ? 'var(--sidebar-active-bg)'
                  : isHover
                  ? 'rgba(255,255,255,0.05)'
                  : 'transparent',
                transition: 'all 0.13s ease',
                marginBottom: 1,
                position: 'relative',
              }}
            >
              {isActive && (
                <div style={{
                  position: 'absolute',
                  left: -1, top: '50%',
                  transform: 'translateY(-50%)',
                  width: 3, height: '55%',
                  background: 'var(--sidebar-accent)',
                  borderRadius: '0 2px 2px 0',
                }} />
              )}
              <Icon />
              <span style={{
                fontSize: 13,
                fontWeight: isActive ? 600 : 400,
                flex: 1,
                textAlign: 'left',
                letterSpacing: '-0.1px',
              }}>
                {item.label}
              </span>
              {item.badge && (
                <span style={{
                  background: 'rgba(200, 36, 26, 0.18)',
                  border: '1px solid rgba(200, 36, 26, 0.35)',
                  color: '#E87168',
                  fontSize: 10,
                  fontFamily: 'var(--font-mono)',
                  padding: '1px 5px',
                  borderRadius: 99,
                  fontWeight: 700,
                }}>
                  {item.badge}
                </span>
              )}
            </button>
          )
        })}
      </nav>

      {/* Theme toggle */}
      <div style={{ padding: '0 10px 8px' }}>
        <button
          onClick={onToggleTheme}
          style={{
            width: '100%',
            display: 'flex', alignItems: 'center', gap: 9,
            padding: '7px 10px',
            borderRadius: 'var(--radius-sm)',
            color: 'rgba(255,255,255,0.40)',
            background: 'transparent',
            transition: 'all 0.13s ease',
            fontSize: 12,
          }}
          onMouseEnter={e => { e.currentTarget.style.color = 'rgba(255,255,255,0.70)'; e.currentTarget.style.background = 'rgba(255,255,255,0.05)' }}
          onMouseLeave={e => { e.currentTarget.style.color = 'rgba(255,255,255,0.40)'; e.currentTarget.style.background = 'transparent' }}
        >
          {theme === 'light' ? <MoonIcon /> : <SunIcon />}
          <span style={{ letterSpacing: '-0.1px' }}>
            {theme === 'light' ? 'Dark mode' : 'Light mode'}
          </span>
        </button>
      </div>

      {/* Network status */}
      <div style={{
        padding: '0 16px 14px',
        borderTop: '1px solid var(--sidebar-border)',
        paddingTop: 14,
        margin: '0 0 0 0',
      }}>
        <div style={{
          background: 'rgba(255,255,255,0.04)',
          border: '1px solid var(--sidebar-border)',
          borderRadius: 'var(--radius)',
          padding: '10px 12px',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <div style={{
                width: 6, height: 6, borderRadius: '50%',
                background: 'var(--sidebar-accent)',
                animation: 'subtlePulse 2.5s ease-in-out infinite',
              }} />
              <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.50)', fontWeight: 500 }}>
                Network Live
              </span>
            </div>
            <span style={{
              fontSize: 10,
              color: 'var(--sidebar-accent)',
              fontFamily: 'var(--font-mono)',
            }}>
              847/s
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 18 }}>
            {[40, 55, 38, 62, 45, 50, 35, 48, 42, 38].map((h, i) => (
              <div key={i} style={{
                flex: 1,
                height: `${h}%`,
                background: h > 55
                  ? 'rgba(180, 83, 9, 0.7)'
                  : 'rgba(29, 184, 135, 0.5)',
                borderRadius: 2,
              }} />
            ))}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 5 }}>
            <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.20)' }}>Kafka lag</span>
            <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.35)', fontFamily: 'var(--font-mono)' }}>avg 42ms</span>
          </div>
        </div>
      </div>
    </aside>
  )
}
