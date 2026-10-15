/**
 * Region and data quality panel.
 *
 * The point actually queried is shown explicitly, because a custom box is
 * reduced to its centre before the request goes out. Without saying so, a user
 * who drew a wide box would reasonably assume the whole area was analysed.
 */

import type { AnalysisResponse } from '../types'

interface Props {
  data: AnalysisResponse
}

export function RegionPanel({ data }: Props) {
  const { region, data_quality: quality, params, cached, events } = data

  return (
    <div className="rounded-lg border border-line bg-white p-4">
      <h2 className="mb-2 text-sm font-bold text-ink">Location and data quality</h2>

      <dl className="space-y-1 text-xs">
        <div className="flex justify-between gap-2">
          <dt className="text-ink-muted">Region</dt>
          <dd className="font-semibold text-ink">{region.name}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-ink-muted">Point queried</dt>
          <dd className="font-semibold text-ink">
            {region.lat.toFixed(2)}, {region.lon.toFixed(2)}
          </dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-ink-muted">Source</dt>
          <dd className="font-semibold text-ink">{region.source_point}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-ink-muted">Range</dt>
          <dd className="font-semibold text-ink">
            {params.start_year}–{params.end_year}
          </dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-ink-muted">Served from cache</dt>
          <dd className="font-semibold text-ink">{cached ? 'yes' : 'no'}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-ink-muted">Events returned</dt>
          <dd className="font-semibold text-ink">{events.length}</dd>
        </div>
      </dl>

      <div className="mt-3 border-t border-line pt-2">
        <h3 className="mb-1 text-xs font-bold text-ink">Data quality</h3>
        <dl className="space-y-1 text-xs">
          <div className="flex justify-between gap-2">
            <dt className="text-ink-muted">Days retrieved</dt>
            <dd className="font-semibold text-ink">{quality.total_days}</dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt className="text-ink-muted">Still missing</dt>
            <dd className="font-semibold text-ink">{quality.missing_days}</dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt className="text-ink-muted">Interpolated</dt>
            <dd className="font-semibold text-ink">{quality.interpolated_days}</dd>
          </div>
        </dl>

        {quality.low_quality_years.length > 0 ? (
          <p className="mt-2 text-xs text-heat">
            Low-quality years: {quality.low_quality_years.join(', ')}
          </p>
        ) : (
          <p className="mt-2 text-xs text-ink-muted">
            No low-quality years in this range.
          </p>
        )}
      </div>
    </div>
  )
}
