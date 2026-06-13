/* eslint-disable react-refresh/only-export-components -- shared primitives module: mixes components, hooks, and tokens by design */
import { useEffect, useState } from 'react'

/* Shared primitives for the Liquid Glass Ledger system. */

export const RISK_TEXT = {
  critical: 'text-critical',
  high:     'text-high',
  medium:   'text-medium',
  low:      'text-low',
}

export const RISK_VAR = {
  critical: 'var(--critical)',
  high:     'var(--high)',
  medium:   'var(--medium)',
  low:      'var(--low)',
}

const CHIP_TONE = {
  critical: 'text-critical bg-critical/8',
  high:     'text-high bg-high/8',
  medium:   'text-medium bg-medium/8',
  low:      'text-low bg-low/8',
  neutral:  'text-ink-2 bg-hover',
}

export function RiskChip({ level }) {
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-px font-mono text-[10px] font-semibold uppercase tracking-[0.08em] ${CHIP_TONE[level]}`}>
      {level}
    </span>
  )
}

const STATUS_TONE = {
  flagged:            'critical',
  frozen:             'high',
  returned:           'critical',
  partially_returned: 'high',
  stale:              'high',
  reviewing:          'medium',
  delayed:            'medium',
  cleared:            'low',
  processing:         'low',
  authorized:         'low',
  submitted:          'neutral',
  pending:            'neutral',
}

export function StatusChip({ status }) {
  const tone = CHIP_TONE[STATUS_TONE[status] || 'neutral']
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-px text-[10.5px] font-medium capitalize ${tone}`}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}

export function AgeLabel({ minutes, warnAfter, dangerAfter }) {
  const tone =
    minutes >= dangerAfter ? 'text-critical font-semibold' :
    minutes >= warnAfter   ? 'text-high font-semibold' :
    'text-ink-3'
  const display = minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`
  return <span className={`font-mono text-[11px] tnum ${tone}`}>{display}</span>
}

/* Animated numeric counter — eases up to `value` on mount/change. */
export function useCountUp(value, duration = 900, delay = 0) {
  const [displayed, setDisplayed] = useState(0)
  useEffect(() => {
    let frame = null
    let start = null
    const step = (ts) => {
      if (!start) start = ts
      const p = Math.min((ts - start) / duration, 1)
      setDisplayed(Math.round((1 - Math.pow(1 - p, 3)) * value))
      if (p < 1) frame = requestAnimationFrame(step)
    }
    const timer = setTimeout(() => { frame = requestAnimationFrame(step) }, delay)
    return () => { clearTimeout(timer); if (frame !== null) cancelAnimationFrame(frame) }
  }, [value, duration, delay])
  return displayed
}

export function PageHeader({ title, subtitle, children }) {
  return (
    <header className="z-10 flex shrink-0 items-end justify-between gap-6 px-8 pb-6 pt-2">
      <div className="min-w-0">
        <h1 className="font-display text-[26px] font-medium tracking-tight text-ink">{title}</h1>
        {subtitle && <div className="mt-1.5 text-[13px] text-ink-3">{subtitle}</div>}
      </div>
      {children && <div className="flex shrink-0 items-center gap-2 pb-0.5">{children}</div>}
    </header>
  )
}

export function SectionLabel({ children, right }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <span className="text-[11px] font-bold uppercase tracking-[0.12em] text-ink-2">{children}</span>
      {right}
    </div>
  )
}

export function TH({ children, className = '', ...props }) {
  return (
    <th
      className={`whitespace-nowrap px-4 py-2.5 text-left text-[10px] font-bold uppercase tracking-[0.1em] text-ink-3 ${className}`}
      {...props}
    >
      {children}
    </th>
  )
}
