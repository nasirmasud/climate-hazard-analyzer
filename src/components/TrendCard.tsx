/**
 * Trend card, in three states.
 *
 * The direction is always written as words, never conveyed by colour alone
 * (section 12.4.4). A red arrow tells a sighted user something and tells a
 * screen reader user nothing, so the words carry the meaning and colour only
 * reinforces it.
 *
 * The non-significant state is deliberately first-class rather than a footnote:
 * "no clear trend" is the honest answer far more often than not on climate
 * data, and the card should not look like a failure to produce a result.
 */

import type { AnalysisResponse } from '../types'

interface Props {
  data: AnalysisResponse
}

export function TrendCard({ data }: Props) {
  const { trend, period_comparison: periods } = data

  const label =
    trend.direction === 'increasing'
      ? 'Increasing'
      : trend.direction === 'decreasing'
        ? 'Decreasing'
        : 'No clear trend'

  // Colour follows the label, never replaces it.
  const accent =
    trend.direction === 'increasing'
      ? 'text-heat border-heat'
      : trend.direction === 'decreasing'
        ? 'text-trend border-trend'
        : 'text-ink-muted border-line'

  return (
    <div className={`rounded-lg border bg-white p-4 ${accent}`}>
      <h2 className="mb-2 text-sm font-bold text-ink">Trend in event frequency</h2>

      <p className={`text-xl font-bold ${accent.split(' ')[0]}`}>{label}</p>

      <dl className="mt-3 space-y-1 text-xs text-ink-muted">
        <div className="flex justify-between gap-2">
          <dt>Mann-Kendall p-value</dt>
          <dd className="font-semibold text-ink">{trend.mann_kendall_p.toFixed(4)}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Sen&apos;s slope</dt>
          <dd className="font-semibold text-ink">
            {trend.sen_slope_per_year >= 0 ? '+' : ''}
            {trend.sen_slope_per_year.toFixed(3)} /year
          </dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Regression slope</dt>
          <dd className="font-semibold text-ink">
            {trend.regression_slope_per_year >= 0 ? '+' : ''}
            {trend.regression_slope_per_year.toFixed(3)} /year
          </dd>
        </div>
      </dl>

      <p className="mt-3 border-t border-line pt-2 text-xs text-ink-muted">
        Significant at p &lt; 0.05:{' '}
        <span className="font-semibold text-ink">{trend.significant ? 'yes' : 'no'}</span>
        . Mann-Kendall decides the direction; the regression slope only draws the line.
      </p>

      {/* Period comparison. The labels come from the API, so the UI never
          assumes a fixed pair of decades. */}
      <div className="mt-3 border-t border-line pt-2">
        <p className="text-xs font-semibold text-ink">Events per year compared</p>
        <div className="mt-1 grid grid-cols-2 gap-2 text-xs">
          <div>
            <p className="text-ink-muted">{periods.early.label}</p>
            <p className="text-lg font-bold text-ink">
              {periods.early.events_per_year.toFixed(2)}
            </p>
          </div>
          <div>
            <p className="text-ink-muted">{periods.late.label}</p>
            <p className="text-lg font-bold text-ink">
              {periods.late.events_per_year.toFixed(2)}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
