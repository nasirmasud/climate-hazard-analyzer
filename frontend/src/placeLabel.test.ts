import { describe, expect, it } from 'vitest'

import {
  looksLikeCoordinates,
  PLACE_FALLBACK_LABEL,
  resolvePlaceLabel,
  sanitisePlaceName,
} from './placeLabel'

describe('resolvePlaceLabel', () => {
  it('uses the name when one is found', () => {
    expect(resolvePlaceLabel('Dhaka, Bangladesh')).toBe('Dhaka, Bangladesh')
  })

  it('falls back when there is no name', () => {
    expect(resolvePlaceLabel(null)).toBe(PLACE_FALLBACK_LABEL)
    expect(resolvePlaceLabel(undefined)).toBe(PLACE_FALLBACK_LABEL)
    expect(resolvePlaceLabel('')).toBe(PLACE_FALLBACK_LABEL)
  })

  it('falls back when the lookup failed', () => {
    expect(resolvePlaceLabel('Dhaka, Bangladesh', true)).toBe(PLACE_FALLBACK_LABEL)
    expect(resolvePlaceLabel(null, true)).toBe(PLACE_FALLBACK_LABEL)
  })

  it('never returns an empty label, whatever it is given', () => {
    const inputs = [null, undefined, '', '   ', '\n\t', '23.81, 90.41', 'lat=1']
    for (const input of inputs) {
      expect(resolvePlaceLabel(input).length).toBeGreaterThan(0)
    }
  })

  it('keeps the fallback text exactly as specified', () => {
    expect(PLACE_FALLBACK_LABEL).toBe('Custom area (enter a box)')
  })
})

describe('sanitisePlaceName', () => {
  it('collapses internal whitespace', () => {
    expect(sanitisePlaceName('Dhaka,   Bangladesh')).toBe('Dhaka, Bangladesh')
    expect(sanitisePlaceName('Dhaka,\nBangladesh')).toBe('Dhaka, Bangladesh')
  })

  it('trims the ends', () => {
    expect(sanitisePlaceName('  Reykjavik, Iceland  ')).toBe('Reykjavik, Iceland')
  })

  it('shortens a long name on a word boundary', () => {
    const long = 'Some Extremely Long Settlement Name That Will Not Fit'
    const result = sanitisePlaceName(long)!
    expect(result.length).toBeLessThanOrEqual(41)
    expect(result.endsWith('…')).toBe(true)
    // Not cut mid-word.
    expect(result).not.toContain('Ex…')
    expect(long.startsWith(result.slice(0, -1))).toBe(true)
  })

  it('keeps a short name untouched', () => {
    expect(sanitisePlaceName('Khalia, Bangladesh')).toBe('Khalia, Bangladesh')
  })

  it('rejects anything that is really a coordinate', () => {
    expect(sanitisePlaceName('23.81, 90.41')).toBeNull()
    expect(sanitisePlaceName('lat=23.81, lon=90.41')).toBeNull()
    expect(sanitisePlaceName('23.81,90.41')).toBeNull()
    expect(sanitisePlaceName('-33.87, 151.21')).toBeNull()
  })

  it('rejects non-strings and blank strings', () => {
    expect(sanitisePlaceName(null)).toBeNull()
    expect(sanitisePlaceName(undefined)).toBeNull()
    expect(sanitisePlaceName('   ')).toBeNull()
  })
})

describe('looksLikeCoordinates', () => {
  it('detects a bare decimal pair', () => {
    expect(looksLikeCoordinates('23.81, 90.41')).toBe(true)
    expect(looksLikeCoordinates('0, 0')).toBe(true)
  })

  it('detects labelled coordinates', () => {
    expect(looksLikeCoordinates('lat=23.81')).toBe(true)
    expect(looksLikeCoordinates('longitude: 90.41')).toBe(true)
    expect(looksLikeCoordinates('LAT=1')).toBe(true)
  })

  it('does not fire on real place names', () => {
    expect(looksLikeCoordinates('Dhaka, Bangladesh')).toBe(false)
    expect(looksLikeCoordinates('New York, United States')).toBe(false)
    expect(looksLikeCoordinates('Saint Martin')).toBe(false)
  })

  it('does not fire on a number in a name', () => {
    // "Route 66" is a place, not a coordinate.
    expect(looksLikeCoordinates('Route 66, California')).toBe(false)
  })
})
