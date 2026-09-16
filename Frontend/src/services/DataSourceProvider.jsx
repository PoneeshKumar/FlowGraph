/* eslint-disable react-refresh/only-export-components -- provider + hook live together */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { createLiveSource, createSnapshotSource } from './dataSource'

const Ctx = createContext(null)

async function probe() {
  const base = import.meta.env.VITE_API_BASE_URL
  if (base) {
    const live = createLiveSource(base)
    try {
      await live.health()
      return { status: 'ready', mode: 'live', source: live, meta: null, banner: null }
    } catch {
      const snap = createSnapshotSource()
      return { status: 'ready', mode: 'demo', source: snap, meta: await snap.meta(),
               banner: `Backend at ${base} is unreachable — showing the bundled snapshot.` }
    }
  }
  const snap = createSnapshotSource()
  return { status: 'ready', mode: 'demo', source: snap, meta: await snap.meta(), banner: null }
}

export function DataSourceProvider({ children }) {
  const [state, setState] = useState({ status: 'probing', mode: null, source: null, meta: null, banner: null })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let active = true
    probe().then(next => { if (active) setState(next) })
    return () => { active = false }
  }, [attempt])

  const retryLive = useCallback(() => setAttempt(a => a + 1), [])
  const value = useMemo(() => ({ ...state, retryLive }), [state, retryLive])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useDataSource() {
  const v = useContext(Ctx)
  if (!v) throw new Error('useDataSource must be used inside DataSourceProvider')
  return v
}
