import { useDataSource } from '../services/DataSourceProvider'
import { fmtDay } from '../lib/format'

export default function ModePill() {
  const { status, mode, meta, banner, retryLive } = useDataSource()
  if (status !== 'ready') return <span className="text-[11px] text-ink-4">connecting…</span>
  if (mode === 'live') {
    return (
      <span className="flex items-center gap-1.5 text-[11px] text-ink-3">
        <span className="h-1.5 w-1.5 rounded-full bg-accent [animation:pulseSoft_2.4s_ease-in-out_infinite]" />
        Live
      </span>
    )
  }
  const end = meta?.dataset?.end_ts
  return (
    <span className="flex items-center gap-2 text-[11px] text-ink-3" title={banner || 'Bundled snapshot — no backend required'}>
      <span className="h-1.5 w-1.5 rounded-full bg-ink-4" />
      Demo{end ? ` · snapshot ${fmtDay(end)}` : ''}
      {banner && <button type="button" onClick={retryLive} className="font-medium text-accent hover:opacity-70">retry live</button>}
    </span>
  )
}
