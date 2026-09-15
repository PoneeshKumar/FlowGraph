import { useCallback, useEffect, useState } from 'react'

/**
 * Run an async loader whenever `deps` change. Returns { data, error, loading, reload }.
 * State updates happen after an await, so the effect never calls setState synchronously
 * (react-hooks/set-state-in-effect).
 */
export function useAsync(fn, deps) {
  const key = JSON.stringify(deps)
  const [nonce, setNonce] = useState(0)
  const [state, setState] = useState({ key: null, data: null, error: null })

  useEffect(() => {
    let active = true
    Promise.resolve()
      .then(() => fn())
      .then(
        data => { if (active) setState({ key, data, error: null }) },
        error => { if (active) setState({ key, data: null, error }) },
      )
    return () => { active = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce])

  const reload = useCallback(() => setNonce(n => n + 1), [])
  const loading = state.key !== key
  return { data: loading ? null : state.data, error: loading ? null : state.error, loading, reload }
}
