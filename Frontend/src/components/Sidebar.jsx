import { motion } from 'motion/react'
import { useState } from 'react'
import ModePill from './ModePill'
import { useDataSource } from '../services/DataSourceProvider'

const NAV = [
  { id: 'overview',     label: 'Overview' },
  { id: 'graph',        label: 'Graph' },
  { id: 'alerts',       label: 'Alerts' },
  { id: 'transactions', label: 'Transactions' },
  { id: 'pipeline',     label: 'Pipeline' },
  { id: 'upload',       label: 'Upload', liveOnly: true },
]

// Live graph mark — still when idle; rotates + drifts on hover
function FlowLogo({ active }) {
  const hub = { x: 16.5, y: 14 }
  const nodes = [
    { x: 8.5,  y: 23.5, r: 4.6, drift: [0, -1.6, 1, 0],  driftY: [0, 1.2, -1.4, 0] },
    { x: 24.5, y: 7.5,  r: 3,   drift: [0, 1.5, -1, 0],  driftY: [0, -1.3, 0.9, 0] },
    { x: 24,   y: 22,   r: 3,   drift: [0, -1.2, 1.6, 0], driftY: [0, 1.4, -1, 0] },
  ]

  return (
    <svg viewBox="0 0 32 32" fill="none" className="h-7 w-7 shrink-0" aria-hidden="true">
      <defs>
        <linearGradient id="fg-grad" x1="6" y1="26" x2="26" y2="6" gradientUnits="userSpaceOnUse">
          <stop stopColor="#0a8761" />
          <stop offset="1" stopColor="#13b884" />
        </linearGradient>
      </defs>

      {/* whole graph rotates around the hub when active */}
      <motion.g
        animate={{ rotate: active ? 360 : 0 }}
        transition={
          active
            ? { duration: 7, repeat: Infinity, ease: 'linear' }
            : { duration: 0.8, ease: 'easeOut' }
        }
        style={{ transformOrigin: `${hub.x}px ${hub.y}px` }}
      >
        <motion.g
          stroke="url(#fg-grad)"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          animate={{ opacity: active ? 0.85 : 0.55 }}
          transition={{ duration: 0.3 }}
        >
          <path d={`M${nodes[0].x} ${nodes[0].y} L${hub.x} ${hub.y}`} />
          <path d={`M${hub.x} ${hub.y} L${nodes[1].x} ${nodes[1].y}`} />
          <path d={`M${hub.x} ${hub.y} L${nodes[2].x} ${nodes[2].y}`} />
        </motion.g>

        {/* nodes drift in/out while the group spins */}
        {nodes.map((n, i) => (
          <motion.circle
            key={i}
            cx={n.x}
            cy={n.y}
            r={n.r}
            fill="url(#fg-grad)"
            animate={active ? { x: n.drift, y: n.driftY } : { x: 0, y: 0 }}
            transition={
              active
                ? { duration: 3.2, repeat: Infinity, ease: 'easeInOut', delay: i * 0.15 }
                : { duration: 0.6, ease: 'easeOut' }
            }
          />
        ))}
      </motion.g>

      {/* hub — stationary pivot, pulses only when active */}
      <motion.circle
        cx={hub.x}
        cy={hub.y}
        r="3.4"
        fill="#fafbfc"
        stroke="url(#fg-grad)"
        strokeWidth="2.2"
        animate={{ scale: active ? [1, 1.16, 1] : 1 }}
        transition={
          active
            ? { duration: 1.8, repeat: Infinity, ease: 'easeInOut' }
            : { duration: 0.4 }
        }
        style={{ transformOrigin: `${hub.x}px ${hub.y}px` }}
      />
      <circle cx={hub.x} cy={hub.y} r="1.15" fill="url(#fg-grad)" />
    </svg>
  )
}

export default function Sidebar({ active, onNav, alertCount }) {
  const [logoHover, setLogoHover] = useState(false)
  const { mode } = useDataSource()
  const items = NAV.filter(item => !item.liveOnly || mode === 'live')

  return (
    <header className="relative z-10 flex shrink-0 items-center gap-8 px-8 py-4">
      {/* Wordmark */}
      <button
        onClick={() => onNav('overview')}
        onMouseEnter={() => setLogoHover(true)}
        onMouseLeave={() => setLogoHover(false)}
        className="flex shrink-0 items-center gap-2.5"
      >
        <FlowLogo active={logoHover} />
        <span className="font-display text-[18px] font-medium tracking-tight text-ink">FlowGraph</span>
      </button>

      {/* Horizontal nav — underline active, no pills or boxes */}
      <nav className="flex min-w-0 flex-1 items-center gap-6">
        {items.map(item => {
          const isActive = active === item.id
          const badge = item.id === 'alerts' && alertCount ? alertCount : null
          return (
            <button
              key={item.id}
              onClick={() => onNav(item.id)}
              className={`relative flex shrink-0 items-center gap-2 pb-0.5 text-[13px] transition-colors duration-200
                ${isActive ? 'font-semibold text-ink' : 'font-normal text-ink-3 hover:text-ink-2'}`}
            >
              {item.label}
              {badge != null && (
                <span className="font-mono text-[10px] font-bold text-critical tnum">{badge.toLocaleString()}</span>
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

      {/* Data-source mode — live backend or bundled snapshot */}
      <div className="hidden shrink-0 items-center md:flex">
        <ModePill />
      </div>
    </header>
  )
}
