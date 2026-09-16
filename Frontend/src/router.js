import { useCallback, useEffect, useState } from 'react'

export const VIEWS = ['overview', 'graph', 'alerts', 'transactions', 'pipeline', 'upload']

export function parseHash(hash) {
  const raw = String(hash || '').replace(/^#\/?/, '')
  const [path, qs] = raw.split('?')
  const view = VIEWS.includes(path) ? path : 'overview'
  const params = Object.fromEntries(new URLSearchParams(qs || ''))
  return { view, params }
}

export function buildHash(view, params = {}) {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  const qs = new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString()
  return `#/${view}${qs ? `?${qs}` : ''}`
}

/** Hash routing: no server rewrites needed, deep links work on a static host. */
export function useRoute() {
  const [route, setRoute] = useState(() => parseHash(window.location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  const navigate = useCallback((view, params) => {
    window.location.hash = buildHash(view, params)
  }, [])
  return { view: route.view, params: route.params, navigate }
}
