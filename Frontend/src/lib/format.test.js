import { describe, it, expect } from 'vitest'
import { fmtAmount, fmtTs, fmtDay, shortId, flagLabel, fmtCompact } from './format'

describe('format', () => {
  it('formats money in major units with no symbol', () => {
    expect(fmtAmount(123456)).toBe('1,234.56')
    expect(fmtAmount(500)).toBe('5.00')
  })
  it('formats dataset timestamps in UTC, never relative', () => {
    expect(fmtDay(1663517880)).toBe('Sep 18, 2022')
    expect(fmtTs(1661991600)).toBe('Sep 1, 00:20')   // 1,200 s after the dataset's first timestamp
  })
  it('shortens ids and labels flag types', () => {
    expect(shortId('e5c379536a14f3eca471395d189aa2fc')).toBe('e5c37953')
    expect(flagLabel('AGGREGATE')).toBe('Aggregate risk')
    expect(flagLabel('WHATEVER')).toBe('Whatever')
    expect(fmtCompact(1895167)).toBe('1.9M')
  })
})
