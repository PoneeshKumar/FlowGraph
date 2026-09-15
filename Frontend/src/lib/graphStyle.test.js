import { describe, it, expect } from 'vitest'
import { communityColor, gnnHeat, prSize, layoutFor } from './graphStyle'

describe('graphStyle', () => {
  it('assigns stable, distinct-ish colours per community', () => {
    expect(communityColor('abc')).toBe(communityColor('abc'))
    expect(communityColor('abc')).not.toBe(communityColor('abd'))
    expect(communityColor(null)).toBe('#a4b0c4')
  })
  it('maps gnn score to the risk ladder using literal colours (cytoscape cannot resolve CSS vars)', () => {
    expect(gnnHeat(0.9)).toBe('#d83a30'); expect(gnnHeat(0.7)).toBe('#c46a10')
    expect(gnnHeat(0.5)).toBe('#9c7c14'); expect(gnnHeat(0.1)).toBe('#0d9d72'); expect(gnnHeat(null)).toBe('#a4b0c4')
  })
  it('sizes by pagerank rank within the view', () => {
    expect(prSize(0, 0, 1)).toBe(18); expect(prSize(1, 0, 1)).toBe(54); expect(prSize(5, 5, 5)).toBe(18)
  })
  it('picks a force layout for small graphs and concentric for big ones', () => {
    expect(layoutFor(100).name).toBe('cose-bilkent'); expect(layoutFor(2000).name).toBe('concentric')
  })
})
