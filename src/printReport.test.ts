import { describe, expect, it, vi } from 'vitest'

import { printFilename, printReport } from './printReport'
import type { PrintTarget } from './printReport'
import type { AnalysisParams } from './types'

const PRESET: AnalysisParams = {
  region: 'dhaka',
  start_year: 1990,
  end_year: 2025,
  hazard: 'heatwave',
}

const BOX: AnalysisParams = {
  lat: 23.81,
  lon: 90.41,
  min_lat: 23.4,
  max_lat: 24.2,
  min_lon: 90.0,
  max_lon: 90.8,
  start_year: 1990,
  end_year: 2025,
}

/** A document title and a print call that can be inspected afterwards. */
function fakeTarget(startTitle = 'Climate Hazard Trend Analyzer') {
  let title = startTitle

  const target: PrintTarget = {
    getTitle: () => title,
    setTitle: (next: string) => {
      title = next
    },
    print: vi.fn(),
  }

  return {
    target,
    title: () => title,
    printed: target.print as ReturnType<typeof vi.fn>,
  }
}

describe('printFilename', () => {
  it('describes a preset run by its id and range', () => {
    expect(printFilename(PRESET)).toBe('dhaka-heatwaves-1990-2025')
  })

  it('matches the backend CSV filename apart from the extension', () => {
    // app/routes.py builds `f"{slug}-heatwaves-{start}-{end}.csv"` from the
    // resolved region name. The frontend only has the id, so the two agree in
    // shape rather than character for character.
    expect(printFilename(PRESET)).toMatch(/^dhaka-heatwaves-\d{4}-\d{4}$/)
  })

  it('falls back to the rounded centre point for a box', () => {
    expect(printFilename(BOX)).toBe('23.81_90.41-heatwaves-1990-2025')
  })

  it('rounds the coordinates, so a stray float cannot reach a filename', () => {
    const noisy: AnalysisParams = { ...BOX, lat: 23.814999, lon: 90.405001 }
    expect(printFilename(noisy)).toBe('23.81_90.41-heatwaves-1990-2025')
  })

  it('copes with neither a preset nor a centre point', () => {
    const empty: AnalysisParams = { start_year: 1990, end_year: 2025 }
    expect(printFilename(empty)).toBe('custom-area-heatwaves-1990-2025')
  })

  it('prefers the preset id when both are present', () => {
    const both: AnalysisParams = { ...PRESET, ...BOX, region: 'mumbai' }
    expect(printFilename(both)).toBe('mumbai-heatwaves-1990-2025')
  })

  it('never produces a path separator or a character a filesystem rejects', () => {
    // The string becomes a filename, so a slash in it would be a directory
    // traversal rather than a label.
    const hostile: AnalysisParams = { region: 'a/b\\c:d*e?f"g<h>i|j', start_year: 1990, end_year: 2025 }
    expect(printFilename(hostile)).not.toMatch(/[/\\:*?"<>|]/)
  })
})

describe('printReport', () => {
  it('prints', () => {
    const { target, printed } = fakeTarget()

    printReport(PRESET, target)

    expect(printed).toHaveBeenCalledTimes(1)
  })

  it('names the file for the analysis while it prints', () => {
    const seen: string[] = []
    let title = 'Climate Hazard Trend Analyzer'
    const target: PrintTarget = {
      getTitle: () => title,
      setTitle: (next: string) => {
        title = next
        seen.push(next)
      },
      // The title at the moment printing is the one the browser would use.
      print: vi.fn(() => {
        seen.push(`printed-as:${title}`)
      }),
    }

    printReport(PRESET, target)

    expect(seen).toEqual([
      'dhaka-heatwaves-1990-2025',
      'printed-as:dhaka-heatwaves-1990-2025',
      'Climate Hazard Trend Analyzer',
    ])
  })

  it('restores the previous title', () => {
    const { target, title } = fakeTarget()

    printReport(PRESET, target)

    expect(title()).toBe('Climate Hazard Trend Analyzer')
  })

  it('restores the title even when printing throws', () => {
    // A cancelled dialog, or a browser that refuses, must not leave the tab
    // claiming to be a report.
    const { target, title } = fakeTarget()
    target.print = vi.fn(() => {
      throw new Error('dialog dismissed')
    })

    expect(() => printReport(PRESET, target)).toThrow('dialog dismissed')
    expect(title()).toBe('Climate Hazard Trend Analyzer')
  })

  it('re-prints cleanly twice in a row', () => {
    // Guards the obvious regression from restoring the title: if the restore
    // wrote the report name back instead of the original, the second print
    // would be named after the first.
    const { target, title, printed } = fakeTarget()

    printReport(PRESET, target)
    printReport({ ...PRESET, region: 'karachi' }, target)

    expect(printed).toHaveBeenCalledTimes(2)
    expect(title()).toBe('Climate Hazard Trend Analyzer')
  })

  it('leaves the title alone when there is nothing to describe', () => {
    const { target, title } = fakeTarget()
    const empty: AnalysisParams = { start_year: 1990, end_year: 2025 }

    printReport(empty, target)

    expect(title()).toBe('Climate Hazard Trend Analyzer')
  })
})