import { motion, AnimatePresence } from 'motion/react'

const NAV = [
  { id: 'dashboard',    label: 'Overview',     icon: GridIcon },
  { id: 'graph',        label: 'Graph',        icon: GraphIcon },
  { id: 'alerts',       label: 'Alerts',       icon: AlertIcon, badge: 17 },
  { id: 'transactions', label: 'Transactions', icon: TxnIcon },
]

function GridIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
      <rect x="1.2" y="1.2" width="5.3" height="5.3" rx="1.6" stroke="currentColor" strokeWidth="1.2"/>
      <rect x="8.5" y="1.2" width="5.3" height="5.3" rx="1.6" stroke="currentColor" strokeWidth="1.2"/>
      <rect x="1.2" y="8.5" width="5.3" height="5.3" rx="1.6" stroke="currentColor" strokeWidth="1.2"/>
      <rect x="8.5" y="8.5" width="5.3" height="5.3" rx="1.6" stroke="currentColor" strokeWidth="1.2" fill="currentColor" fillOpacity=".25"/>
    </svg>
  )
}
function GraphIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
      <circle cx="7.5" cy="7.5" r="2" stroke="currentColor" strokeWidth="1.2"/>
      <circle cx="2.2" cy="3.2" r="1.3" fill="currentColor" opacity=".7"/>
      <circle cx="12.8" cy="3.2" r="1.3" fill="currentColor" opacity=".7"/>
      <circle cx="2.2" cy="11.8" r="1.3" fill="currentColor" opacity=".7"/>
      <circle cx="12.8" cy="11.8" r="1.3" fill="currentColor" opacity=".7"/>
      <path d="M3.3 4.2L6 6M11.7 4.2L9 6M3.3 10.8L6 9M11.7 10.8L9 9" stroke="currentColor" strokeWidth="1" opacity=".5"/>
    </svg>
  )
}
function AlertIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
      <path d="M7.5 1.8L13.6 12.2H1.4L7.5 1.8Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/>
      <path d="M7.5 5.6v3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      <circle cx="7.5" cy="10.5" r=".75" fill="currentColor"/>
    </svg>
  )
}
function TxnIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
      <path d="M2 5h9.5M9 2.5L11.5 5 9 7.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M13 10H3.5M6 7.5L3.5 10 6 12.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" opacity=".55"/>
    </svg>
  )
}
function SunIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
      <circle cx="6.5" cy="6.5" r="2.4" stroke="currentColor" strokeWidth="1.2"/>
      <path d="M6.5.8v1.4M6.5 10.8v1.4M.8 6.5h1.4M10.8 6.5h1.4M2.5 2.5l1 1M9.5 9.5l1 1M10.5 2.5l-1 1M3.5 9.5l-1 1" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  )
}
function MoonIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
      <path d="M11 8.1A4.8 4.8 0 015 2a4.9 4.9 0 102.5 9.5A4.85 4.85 0 0011 8.1z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/>
    </svg>
  )
}

const BARS = [40, 58, 36, 64, 47, 52, 33, 49, 61, 41]

export default function Sidebar({ active, onNav, theme, onToggleTheme }) {
  return (
    <aside className="glass-strong relative z-10 m-4 mr-0 flex w-[226px] shrink-0 flex-col self-stretch rounded-2xl">

      {/* Wordmark */}
      <div className="flex items-center gap-3 border-b border-line px-5 pb-4 pt-5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-accent/15 ring-1 ring-accent/30">
          <svg width="15" height="15" viewBox="0 0 14 14" fill="none">
            <circle cx="7" cy="7" r="2" fill="var(--accent)"/>
            <path d="M7 1v3.4M7 9.6V13M1 7h3.4M9.6 7H13" stroke="var(--accent)" strokeWidth="1.4" strokeLinecap="round"/>
          </svg>
        </div>
        <div>
          <div className="font-display text-[16px] font-medium tracking-tight text-ink">FlowGraph</div>
          <div className="mt-px text-[9.5px] font-semibold uppercase tracking-[0.14em] text-ink-4">
            Network Intelligence
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 pt-4">
        <div className="mb-2 px-2 text-[10px] font-bold uppercase tracking-[0.13em] text-ink-4">
          Workspace
        </div>
        {NAV.map(item => {
          const isActive = active === item.id
          const Icon = item.icon
          return (
            <button
              key={item.id}
              onClick={() => onNav(item.id)}
              className={`group relative mb-0.5 flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] transition-colors duration-200
                ${isActive ? 'text-accent' : 'text-ink-3 hover:text-ink-2'}`}
            >
              {isActive && (
                <motion.span
                  layoutId="nav-pill"
                  className="absolute inset-0 rounded-lg bg-accent/10 ring-1 ring-accent/25"
                  transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                />
              )}
              <span className="relative z-[1] transition-transform duration-200 group-hover:scale-110">
                <Icon />
              </span>
              <span className={`relative z-[1] flex-1 text-left tracking-tight ${isActive ? 'font-semibold' : 'font-normal'}`}>
                {item.label}
              </span>
              {item.badge && (
                <span className="relative z-[1] rounded-full border border-critical/30 bg-critical/10 px-1.5 py-px font-mono text-[10px] font-bold text-critical tnum">
                  {item.badge}
                </span>
              )}
            </button>
          )
        })}
      </nav>

      {/* Theme toggle */}
      <div className="px-3 pb-2">
        <button
          onClick={onToggleTheme}
          className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs text-ink-3 transition-colors duration-200 hover:bg-hover hover:text-ink-2"
        >
          <span className="relative h-[13px] w-[13px]">
            <AnimatePresence mode="wait" initial={false}>
              <motion.span
                key={theme}
                className="absolute inset-0 flex items-center justify-center"
                initial={{ rotate: -90, opacity: 0, scale: 0.6 }}
                animate={{ rotate: 0, opacity: 1, scale: 1 }}
                exit={{ rotate: 90, opacity: 0, scale: 0.6 }}
                transition={{ duration: 0.25, ease: [0.32, 0.72, 0, 1] }}
              >
                {theme === 'light' ? <MoonIcon /> : <SunIcon />}
              </motion.span>
            </AnimatePresence>
          </span>
          {theme === 'light' ? 'Dark appearance' : 'Light appearance'}
        </button>
      </div>

      {/* Stream health */}
      <div className="border-t border-line px-4 pb-4 pt-3.5">
        <div className="glass-soft rounded-xl px-3 py-2.5">
          <div className="mb-2 flex items-center justify-between">
            <span className="flex items-center gap-1.5 text-[11px] font-medium text-ink-2">
              <span className="h-1.5 w-1.5 rounded-full bg-accent [animation:pulseSoft_2.4s_ease-in-out_infinite]" />
              Stream live
            </span>
            <span className="font-mono text-[10px] text-accent tnum">847/s</span>
          </div>
          <div className="flex h-[18px] items-end gap-0.5">
            {BARS.map((h, i) => (
              <motion.span
                key={i}
                className={`flex-1 rounded-sm ${h > 55 ? 'bg-high/70' : 'bg-accent/50'}`}
                initial={{ height: 0 }}
                animate={{ height: `${h}%` }}
                transition={{ delay: 0.3 + i * 0.05, type: 'spring', stiffness: 200, damping: 22 }}
              />
            ))}
          </div>
          <div className="mt-1.5 flex justify-between text-[9px]">
            <span className="text-ink-4">Kafka lag</span>
            <span className="font-mono text-ink-3 tnum">avg 42ms</span>
          </div>
        </div>
      </div>
    </aside>
  )
}
