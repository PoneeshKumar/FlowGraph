import { describe, it, expect } from 'vitest'
import { createSnapshotSource, interpolateCurve, NotInSnapshotError } from './dataSource'

const curve = { loaded: true, cutoffs: [0.5, 0.6, 0.7], precision: [0.2, 0.4, 0.8], recall: [0.9, 0.6, 0.3], tp: [90, 60, 30], fp: [360, 90, 8], marked: [450, 150, 38] }

describe('interpolateCurve', () => {
  it('interpolates linearly between cutoffs and clamps at the ends', () => {
    const m = interpolateCurve(curve, 0.65)
    expect(m.precision).toBeCloseTo(0.6) ; expect(m.recall).toBeCloseTo(0.45) ; expect(m.tp).toBe(45)
    expect(interpolateCurve(curve, 0.1).precision).toBe(0.2)
    expect(interpolateCurve(curve, 0.9).precision).toBe(0.8)
    expect(interpolateCurve({ loaded: false }, 0.5)).toEqual({ loaded: false })
  })
})

describe('createSnapshotSource', () => {
  const files = {
    'meta.json': { featured_count: 1 },
    'featured.json': ['a1'],
    'alerts.json': { total: 3, items: [
      { id: 1, flag_type: 'AGGREGATE', risk_level: 'critical', risk_score: 0.9 },
      { id: 2, flag_type: 'CYCLE', risk_level: 'high', risk_score: 0.7 },
      { id: 3, flag_type: 'AGGREGATE', risk_level: 'low', risk_score: 0.2 },
    ] },
    'transactions.json': { latest: [{ txn_id: 't', currency: 'Euro' }, { txn_id: 'u', currency: 'US Dollar' }], by_account: { a1: [{ txn_id: 'v' }] } },
    'graph/a1.json': { nodes: [], edges: [] },
    'accounts/a1.json': { node: { id: 'a1' }, explanation: { explanation: 'e' } },
    'pipeline/metrics_curve.json': curve,
    'pipeline/communities.json': [{ community_id: 'c', size: 3, risk_score: 0.5 }, { community_id: 'd', size: 9, risk_score: 0.1 }],
  }
  const src = createSnapshotSource(path => Promise.resolve(files[path]))

  it('filters and pages alerts in memory', async () => {
    const page = await src.alerts.list({ flag_type: 'AGGREGATE', min_level: 'low', limit: 1, offset: 0 })
    expect(page.total).toBe(2) ; expect(page.items[0].id).toBe(1)
    const high = await src.alerts.list({ min_level: 'high' })
    expect(high.items.map(a => a.id)).toEqual([1, 2])
  })
  it('serves featured graph/account data and rejects the rest', async () => {
    expect((await src.graph.subgraph('a1', 2)).nodes).toEqual([])
    expect((await src.ai.explain('a1')).explanation).toBe('e')
    await expect(src.graph.subgraph('zz', 2)).rejects.toBeInstanceOf(NotInSnapshotError)
    await expect(src.pipeline.run()).rejects.toBeInstanceOf(NotInSnapshotError)
  })
  it('filters transactions by currency and account', async () => {
    expect((await src.transactions.list({ currency: 'Euro' })).items.map(t => t.txn_id)).toEqual(['t'])
    expect((await src.transactions.list({ account_id: 'a1' })).items.map(t => t.txn_id)).toEqual(['v'])
  })
  it('sorts communities and interpolates metrics', async () => {
    expect((await src.pipeline.communities({ sort: 'size' }))[0].community_id).toBe('d')
    expect((await src.pipeline.metrics(0.65)).precision).toBeCloseTo(0.6)
  })
  it('exposes the dataset from meta and rejects uploads', async () => {
    const s2 = createSnapshotSource(path => Promise.resolve({ 'meta.json': { dataset: { name: 'T' } } }[path]))
    expect((await s2.datasets.current()).name).toBe('T')
    expect(s2.datasets.templateUrl()).toBeNull()
    await expect(s2.datasets.upload({})).rejects.toBeInstanceOf(NotInSnapshotError)
  })
})
