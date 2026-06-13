import { motion } from 'motion/react'

const NAV = [
  { id: 'dashboard',    label: 'Overview' },
  { id: 'graph',        label: 'Graph' },
  { id: 'alerts',       label: 'Alerts', badge: 17 },
  { id: 'transactions', label: 'Transactions' },
]

export default function Sidebar({ active, onNav }) {
  return (
    <header className="relative z-10 flex shrink-0 items-center gap-8 px-8 py-4">
      {/* Wordmark */}
      <button onClick={() => onNav('dashboard')} className="flex shrink-0 items-baseline gap-2">
        <span className="font-display text-[18px] font-medium tracking-tight text-ink">FlowGraph</span>
        <span className="hidden text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-4 sm:inline">
          Network Intelligence
        </span>
      </button>

      {/* Horizontal nav — underline active, no pills or boxes */}
      <nav className="flex min-w-0 flex-1 items-center gap-6">
        {NAV.map(item => {
          const isActive = active === item.id
          return (
            <button
              key={item.id}
              onClick={() => onNav(item.id)}
              className={`relative flex shrink-0 items-center gap-2 pb-0.5 text-[13px] transition-colors duration-200
                ${isActive ? 'font-semibold text-ink' : 'font-normal text-ink-3 hover:text-ink-2'}`}
            >
              {item.label}
              {item.badge && (
                <span className="font-mono text-[10px] font-bold text-critical tnum">{item.badge}</span>
              )}
              {isActive && (
                <motion.span
                  layoutId="nav-underline"
                  className="absolute -bottom-1 left-0 right-0 h-[2px] rounded-full bg-accent"
                  transition={{ type: 'spring', stiffness: 480, damping: 36 }}
                />
              )}
            </button>
          )
        })}
      </nav>

      {/* Stream — inline, no boxed footer */}
      <div className="hidden shrink-0 items-center gap-3 text-[11px] text-ink-3 md:flex">
        <span className="flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-accent [animation:pulseSoft_2.4s_ease-in-out_infinite]" />
          Live
        </span>
        <span className="font-mono text-accent tnum">847/s</span>
        <span className="text-ink-4">·</span>
        <span className="font-mono tnum">42ms lag</span>
      </div>
    </header>
  )
}
