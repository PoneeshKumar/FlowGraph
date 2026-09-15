import axios from 'axios'

export class NotInSnapshotError extends Error {
  constructor(what) {
    super(`${what} needs a live backend — it isn't part of the demo snapshot.`)
    this.name = 'NotInSnapshotError'
  }
}

const LEVEL_RANK = { low: 1, medium: 2, high: 3, critical: 4 }

/** Linear interpolation over a precomputed precision/recall curve. */
export function interpolateCurve(curve, cutoff) {
  if (!curve || !curve.loaded) return { loaded: false }
  const xs = curve.cutoffs
  if (cutoff <= xs[0]) return pick(curve, 0)
  if (cutoff >= xs[xs.length - 1]) return pick(curve, xs.length - 1)
  let i = 0
  while (xs[i + 1] < cutoff) i++
  const t = (cutoff - xs[i]) / (xs[i + 1] - xs[i])
  const lerp = (arr) => arr[i] + (arr[i + 1] - arr[i]) * t
  return {
    loaded: true, cutoff,
    precision: lerp(curve.precision), recall: lerp(curve.recall),
    tp: Math.round(lerp(curve.tp)), fp: Math.round(lerp(curve.fp)), marked: Math.round(lerp(curve.marked)),
  }
}
function pick(curve, i) {
  return { loaded: true, cutoff: curve.cutoffs[i], precision: curve.precision[i], recall: curve.recall[i],
           tp: curve.tp[i], fp: curve.fp[i], marked: curve.marked[i] }
}

// ---------------------------------------------------------------- live -----

export function createLiveSource(baseUrl) {
  const base = String(baseUrl).replace(/\/+$/, '')
  const root = base.replace(/\/api$/, '')
  const http = axios.create({ baseURL: base, timeout: 30000 })
  const get = (url, params) => http.get(url, { params }).then(r => r.data)
  return {
    mode: 'live',
    health: () => axios.get(`${root}/health`, { timeout: 2500 }).then(r => r.data),
    featured: async () => [],
    stats: {
      overview: () => get('/stats/overview'),
      series: (currency, period) => get('/stats/volume-series', { currency, period }),
    },
    alerts: {
      list: (p = {}) => get('/alerts', p),
      setStatus: (id, status) => http.patch(`/alerts/${id}/status`, { status }).then(r => r.data),
    },
    transactions: { list: (p = {}) => get('/transactions', p) },
    graph: {
      subgraph: (account_id, depth = 2) => get('/graph/subgraph', { account_id, depth }),
      shortestPath: (account_a, account_b) => get('/graph/shortest-path', { account_a, account_b }),
      flow: (account_a, account_b, window = '7d') => get('/graph/flow', { account_a, account_b, window }),
      account: (id) => get(`/graph/account/${encodeURIComponent(id)}`),
    },
    risk: { evaluate: (id, params = {}) => http.post(`/risk/evaluate/${encodeURIComponent(id)}`, null, { params }).then(r => r.data) },
    ai: { explain: (id) => get(`/accounts/${encodeURIComponent(id)}/enrich`) },
    pipeline: {
      overview: (metric = 'gnn', limit = 600) => get('/pipeline/overview', { metric, limit }),
      communities: (p = {}) => get('/pipeline/communities', p),
      subgraph: (p = {}) => get('/pipeline/subgraph', p),
      marked: (p = {}) => get('/pipeline/marked', p),
      threshold: () => get('/pipeline/threshold'),
      metrics: (cutoff) => get('/pipeline/metrics', { cutoff }),
      metricsCurve: (points = 46) => get('/pipeline/metrics/curve', { points }),
      run: () => http.post('/pipeline/run').then(r => r.data),
      runStatus: (id) => get(`/pipeline/run/${id}`),
      latestRun: () => get('/pipeline/run/latest'),
    },
    system: { llm: () => get('/system/llm') },
    datasets: {
      current: () => get('/datasets/current'),
      templateUrl: () => `${base}/datasets/template`,
      upload: ({ transactions, patterns }, onProgress) => {
        const fd = new FormData()
        fd.append('transactions', transactions)
        if (patterns) fd.append('patterns', patterns)
        fd.append('confirm', 'replace')
        return http.post('/datasets/upload', fd, {
          timeout: 0,
          onUploadProgress: e => onProgress?.(e.total ? e.loaded / e.total : 0),
        }).then(r => r.data)
      },
    },
  }
}

// ------------------------------------------------------------ snapshot -----

// Vite turns this into lazy chunks: one small import per JSON file, on demand.
const modules = import.meta.glob('../data/snapshot/**/*.json')

function defaultLoader(path) {
  const key = `../data/snapshot/${path}`
  const mod = modules[key]
  if (!mod) return Promise.resolve(undefined)
  return mod().then(m => m.default)
}

/** `loader(path) -> Promise<json|undefined>`; injectable for tests. */
export function createSnapshotSource(loader = defaultLoader) {
  const cache = new Map()
  const load = (path) => {
    if (!cache.has(path)) cache.set(path, loader(path))
    return cache.get(path)
  }
  const need = async (path, what) => {
    const v = await load(path)
    if (v === undefined) throw new NotInSnapshotError(what)
    return v
  }
  const unsupported = (what) => () => Promise.reject(new NotInSnapshotError(what))

  return {
    mode: 'demo',
    meta: () => load('meta.json'),
    featured: () => load('featured.json').then(v => v || []),
    stats: {
      overview: () => need('overview.json', 'Overview'),
      series: async (currency, period) => {
        const all = await need('series.json', 'Volume series')
        const s = all[currency] && all[currency][period]
        if (!s) throw new NotInSnapshotError(`${currency} ${period} series`)
        return s
      },
    },
    alerts: {
      list: async ({ flag_type, min_level = 'low', limit = 100, offset = 0 } = {}) => {
        const { items } = await need('alerts.json', 'Alerts')
        const min = LEVEL_RANK[min_level] || 1
        const f = items.filter(a => (!flag_type || a.flag_type === flag_type) && (LEVEL_RANK[a.risk_level] || 0) >= min)
        return { total: f.length, items: f.slice(offset, offset + limit) }
      },
      setStatus: async (id, status) => ({ id, status, demo: true }),
    },
    transactions: {
      list: async ({ limit = 50, account_id, currency } = {}) => {
        const { latest, by_account } = await need('transactions.json', 'Transactions')
        let rows = account_id ? by_account[account_id] : latest
        if (account_id && !rows) throw new NotInSnapshotError(`Transactions for ${account_id}`)
        if (currency) rows = rows.filter(t => t.currency === currency)
        return { items: rows.slice(0, limit) }
      },
    },
    graph: {
      subgraph: (id) => need(`graph/${id}.json`, `Neighbourhood of ${id}`),
      shortestPath: async (a, b) => {
        const paths = await need('paths.json', 'Shortest paths')
        const p = paths[`${a}->${b}`]
        if (!p) throw new NotInSnapshotError(`Path ${a} → ${b}`)
        return p
      },
      flow: async (a, b, window = '7d') => {
        const flows = await need('flows.json', 'Flows')
        const f = flows[`${a}->${b}|${window}`]
        if (!f) throw new NotInSnapshotError(`Flow ${a} → ${b}`)
        return f
      },
      account: (id) => need(`accounts/${id}.json`, `Account ${id}`).then(v => v.node),
    },
    risk: { evaluate: unsupported('Risk evaluation') },
    ai: { explain: (id) => need(`accounts/${id}.json`, `Explanation for ${id}`).then(v => v.explanation) },
    pipeline: {
      overview: (metric = 'gnn') => need(`pipeline/overview_${metric === 'pagerank' ? 'pagerank' : 'gnn'}.json`, 'Pipeline overview'),
      communities: async ({ sort = 'risk', limit = 100, offset = 0 } = {}) => {
        const rows = [...await need('pipeline/communities.json', 'Communities')]
        rows.sort((a, b) => sort === 'size' ? b.size - a.size : b.risk_score - a.risk_score)
        return rows.slice(offset, offset + limit)
      },
      subgraph: async ({ account_id } = {}) => {
        if (account_id) return need(`graph/${account_id}.json`, `Neighbourhood of ${account_id}`)
        throw new NotInSnapshotError('Community subgraphs')
      },
      marked: async ({ signal, limit = 100, offset = 0 } = {}) => {
        const rows = await need('pipeline/marked.json', 'Marked accounts')
        const f = signal ? rows.filter(r => r.signals && r.signals[signal]) : rows
        return f.slice(offset, offset + limit)
      },
      threshold: () => need('pipeline/threshold.json', 'Threshold'),
      metrics: async (cutoff) => interpolateCurve(await need('pipeline/metrics_curve.json', 'Metrics'), cutoff),
      metricsCurve: () => need('pipeline/metrics_curve.json', 'Metrics curve'),
      run: unsupported('Running the pipeline'),
      runStatus: () => need('pipeline/latest_run.json', 'Run status'),
      latestRun: () => need('pipeline/latest_run.json', 'Run status'),
    },
    system: { llm: () => load('llm.json').then(v => v || { provider: 'none', model: null, reachable: false }) },
    datasets: {
      current: () => load('meta.json').then(m => m?.dataset ?? null),
      templateUrl: () => null,
      upload: unsupported('Uploading a dataset'),
    },
  }
}
