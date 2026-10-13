/**
 * "Save as PDF", by printing the page.
 *
 * The browser's own print engine writes the PDF, so this costs no dependency and
 * no bundle weight. The output is real vector text and a crisp SVG chart, which
 * is better than a canvas rasterisation of the same page for a document whose
 * whole content is numbers and a line.
 *
 * What it does *not* do is produce a `.pdf` from a click. It opens the print
 * dialog, where "Save as PDF" is one of the destinations. That is the trade for
 * zero dependencies: the PDF library alternative is roughly half the size of
 * this app's entire current bundle.
 *
 * The filename is the part that needs handling. A browser names the file after
 * `document.title`, which on this page is the app's heading, so every saved
 * report would be called "Climate Hazard Trend Analyzer". Setting the title for
 * the duration of the print and restoring it afterwards is what makes the file
 * identifiable — and it has to be restored even if printing throws, or the tab
 * is left with a wrong title.
 */

import type { AnalysisParams } from './types'

/**
 * Strip characters a filesystem or a browser will not accept in a filename.
 *
 * A preset id comes from a fixed dropdown and the backend rejects unknown ids,
 * so nothing hostile should ever reach this. Sanitising anyway because the string
 * does end up as a filename, and a separator in it is a directory rather than a
 * label — cheap to make impossible, awkward to debug if it ever happens.
 */
function slug(value: string): string {
  const cleaned = value.replace(/[/\\:*?"<>|]+/g, '-').trim()
  return cleaned || 'area'
}

/**
 * The document title the browser will use as the PDF filename.
 *
 * Mirrors the CSV export's filename on the backend (`_csv_filename` in
 * `app/routes.py`) so the two downloads of one analysis are recognisably the
 * same run. The backend resolves the preset's display name; the frontend only
 * has the id, so a preset uses the id and a box uses its rounded centre point.
 */
export function printFilename(params: AnalysisParams): string {
  const place =
    params.region ?? (params.lat !== undefined && params.lon !== undefined
      ? `${params.lat.toFixed(2)}_${params.lon.toFixed(2)}`
      : 'custom-area')

  return `${slug(place)}-heatwaves-${params.start_year}-${params.end_year}`
}

/** The browser pieces this module touches, injected so it can be tested. */
export interface PrintTarget {
  getTitle(): string
  setTitle(next: string): void
  print(): void
}

function browserTarget(): PrintTarget {
  return {
    getTitle: () => document.title,
    setTitle: (next: string) => {
      document.title = next
    },
    print: () => window.print(),
  }
}

/**
 * Prints the current page under a filename describing the analysis.
 *
 * The title is restored in a `finally` rather than after the call, because a
 * cancelled print dialog and a browser that throws both leave the page usable
 * and the title still needs to go back.
 */
export function printReport(params: AnalysisParams, target: PrintTarget = browserTarget()): void {
  const previous = target.getTitle()

  target.setTitle(printFilename(params))
  try {
    target.print()
  } finally {
    target.setTitle(previous)
  }
}