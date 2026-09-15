import { describe, it, expect } from 'vitest'
import { parseHash, buildHash } from './router'

describe('router', () => {
  it('parses views and params, defaulting unknown views to overview', () => {
    expect(parseHash('#/graph?account=abc&depth=2')).toEqual({ view: 'graph', params: { account: 'abc', depth: '2' } })
    expect(parseHash('')).toEqual({ view: 'overview', params: {} })
    expect(parseHash('#/nope')).toEqual({ view: 'overview', params: {} })
  })
  it('builds hashes, dropping empty params', () => {
    expect(buildHash('alerts', { id: 7, x: '', y: null })).toBe('#/alerts?id=7')
    expect(buildHash('overview')).toBe('#/overview')
  })
})
