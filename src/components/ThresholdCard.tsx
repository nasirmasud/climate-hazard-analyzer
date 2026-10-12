/**
 * Threshold card, built entirely from the API's `threshold` object.
 *
 * There is no hard-coded "95th percentile / 3+ days / ±7-day window" copy here.
 * If a constant in `heatwave.py` changes, this card changes with it and no
 * frontend edit is needed. That also means this panel cannot drift out of sync
 * with the method that actually produced the numbers beside it.
 */

import type { AnalysisResponse } from '../types'

interface Props {
  data: AnalysisResponse
}

export function ThresholdCard({ data }: Props) {
  const t = data.threshold

  return (
    <div className="rounded-lg border border-line bg-white p-4">
      <h2 className="mb-2 text-sm font-bold text-ink">How a heatwave is defined</h2>

      <p className="text-xs text-ink-muted">
        {t.min_run_days} or more consecutive days above the{' '}
        <span className="font-semibold text-ink">{t.percentile}th percentile</span> for
        that calendar day, measured against the{' '}
        <span className="font-semibold text-ink">
          {t.baseline_start}–{t.baseline_end}
        </span>{' '}
        baseline.
      </p>

      <dl className="mt-3 space-y-1 text-xs text-ink-muted">
        <div className="flex justify-between gap-2">
          <dt>Percentile</dt>
          <dd className="font-semibold text-ink">{t.percentile}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Window around each day</dt>
          <dd className="font-semibold text-ink">±{t.window_days} days</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Minimum run</dt>
          <dd className="font-semibold text-ink">{t.min_run_days} days</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Baseline period</dt>
          <dd className="font-semibold text-ink">
            {t.baseline_start}–{t.baseline_end}
          </dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Units</dt>
          <dd className="font-semibold text-ink">°{t.unit}</dd>
        </div>
      </dl>

      <p className="mt-3 border-t border-line pt-2 text-xs text-ink-muted">
        The baseline is fixed, so changing the year range you analyse does not move the
        threshold.
      </p>
    </div>
  )
}
