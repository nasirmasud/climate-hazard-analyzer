/**
 * Insight sentences and the limitations list.
 *
 * Both come from the API as ready-made strings, generated in `insights.py` from
 * the same numbers the cards and chart use. The frontend deliberately does not
 * recompute or reword them: a second place that could phrase a finding
 * differently is a second place for the UI to disagree with itself.
 */

import type { AnalysisResponse } from '../types'

interface Props {
  data: AnalysisResponse
}

export function InsightsPanel({ data }: Props) {
  return (
    <div className="rounded-lg border border-line bg-white p-4">
      <h2 className="mb-2 text-sm font-bold text-ink">Key insights</h2>

      {data.insights.length > 0 ? (
        <ul className="space-y-2">
          {data.insights.map((sentence) => (
            <li key={sentence} className="flex gap-2 text-xs text-ink">
              <span className="text-heat" aria-hidden="true">
                •
              </span>
              <span>{sentence}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-ink-muted">—</p>
      )}

      <div className="mt-4 border-t border-line pt-3">
        <h3 className="mb-2 text-xs font-bold text-ink">Limitations</h3>
        <ul className="space-y-1.5">
          {data.limitations.map((item) => (
            <li key={item} className="flex gap-2 text-xs text-ink-muted">
              <span aria-hidden="true">–</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
