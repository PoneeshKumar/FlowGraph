const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** amount_cents → "1,234.56" (major units of the row's own currency; no symbol). */
export function fmtAmount(cents) {
  return (Number(cents || 0) / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** major-unit number → "1.2M" / "340K" / "12". */
export function fmtCompact(n) {
  const v = Number(n || 0)
  if (Math.abs(v) >= 1e9) return `${(v / 1e9).toFixed(1)}B`
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(0)}K`
  return `${Math.round(v)}`
}

const utc = (ts) => new Date(Number(ts) * 1000)

/** unix seconds → "Sep 1, 23:40" (UTC — the dataset's own clock). */
export function fmtTs(ts) {
  const d = utc(ts)
  const hh = String(d.getUTCHours()).padStart(2, '0'), mm = String(d.getUTCMinutes()).padStart(2, '0')
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${hh}:${mm}`
}

/** unix seconds → "Sep 18, 2022". */
export function fmtDay(ts) {
  const d = utc(ts)
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`
}

/** unix seconds → "Sep 3" (chart axis). */
export function fmtShortDay(ts) {
  const d = utc(ts)
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`
}

/** unix seconds → "14:30" (chart axis, 24h period). */
export function fmtClock(ts) {
  const d = utc(ts)
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`
}

/** ISO string → "Sep 3, 04:55". */
export function fmtIso(iso) {
  if (!iso) return '—'
  return fmtTs(Date.parse(iso) / 1000)
}

export function shortId(id) {
  return String(id || '').slice(0, 8)
}

const FLAG_LABELS = {
  AGGREGATE: 'Aggregate risk',
  COMMUNITY: 'Community risk',
  CYCLE: 'Circular flow',
  LIVE_GNN: 'Live GNN score',
}

export function flagLabel(type) {
  if (FLAG_LABELS[type]) return FLAG_LABELS[type]
  const t = String(type || '').toLowerCase().replace(/_/g, ' ')
  return t.charAt(0).toUpperCase() + t.slice(1)
}
