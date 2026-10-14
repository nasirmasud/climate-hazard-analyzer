/**
 * Yearly detail table.
 *
 * The header is sticky so column meanings stay visible while scrolling a long
 * range. The table scrolls horizontally on a narrow screen rather than wrapping
 * numbers, because a wrapped temperature is harder to read than a scrolled one.
 *
 * Null averages render as an em dash: a year with no events genuinely has no
 * average duration, and 0 would read as "heatwaves lasted zero days".
 */

import { getExportCsvUrl } from '../api'
import { printReport } from '../printReport'
import type { AnalysisParams, YearlyOut } from '../types'

interface Props {
  yearly: YearlyOut[]
  /**
   * The params that produced these rows, used to build the download URL. Null
   * until a response arrives, which is also when the button is hidden.
   */
  downloadParams: AnalysisParams | null
}

function Num({
  value,
  digits = 2,
  unit,
}: {
  value: number | null
  digits?: number
  unit?: string
}) {
  if (value === null) {
    return <span className="text-ink-muted">—</span>
  }
  return (
    <>
      {value.toFixed(digits)}
      {unit && <span className="ml-0.5 text-ink-muted">{unit}</span>}
    </>
  )
}

export function DataTable({ yearly, downloadParams }: Props) {
  // Named here rather than inlined, so the count above the table can never
  // disagree with the rows below it.
  const lowQuality = yearly.filter((row) => row.low_quality)

  return (
    <div className="rounded-lg border border-line bg-white p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-bold text-ink">Detailed data</h2>
          <p className="mt-0.5 text-xs text-ink-muted">
            {yearly.length} year{yearly.length === 1 ? '' : 's'}
            {lowQuality.length > 0 && (
              <>
                {' · '}
                <span className="text-heat">
                  {lowQuality.length} excluded from the trend
                </span>
              </>
            )}
          </p>
        </div>

        {downloadParams && (
          /* A plain link rather than a fetch-and-blob. The browser handles the
             download natively, shows its own progress, and there is no blob URL
             to revoke. The `download` attribute is only a hint — the
             Content-Disposition header on the response is what actually names
             the file. */
          <div className="flex items-center gap-2 print:hidden">
            <button
              type="button"
              onClick={() => printReport(downloadParams)}
              className="rounded border border-line px-3 py-1.5 text-xs font-semibold text-ink hover:bg-surface"
            >
              Save as PDF
            </button>
            <a
              href={getExportCsvUrl(downloadParams)}
              download
              className="rounded border border-line px-3 py-1.5 text-xs font-semibold text-ink hover:bg-surface"
            >
              Download CSV
            </a>
          </div>
        )}
      </div>

      {lowQuality.length > 0 && (
        <p className="mb-3 rounded border border-heat bg-heat-soft px-3 py-2 text-xs text-heat">
          Low-quality years ({lowQuality.map((row) => row.year).join(', ')}) had more than
          5% of their days missing. They are shown here and counted in the totals, but
          excluded from the trend test.
        </p>
      )}

      <div className="max-h-96 overflow-auto">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-white">
            <tr className="border-b border-line">
              <th scope="col" className="py-2 pr-3 font-semibold">
                Year
              </th>
              <th scope="col" className="py-2 pr-3 font-semibold">
                Events
              </th>
              <th scope="col" className="py-2 pr-3 font-semibold">
                Heatwave days
              </th>
              <th scope="col" className="py-2 pr-3 font-semibold">
                Avg duration
              </th>
              <th scope="col" className="py-2 pr-3 font-semibold">
                Avg intensity
              </th>
              <th scope="col" className="py-2 pr-3 font-semibold">
                Avg max temp
              </th>
              <th scope="col" className="py-2 font-semibold">
                Quality
              </th>
            </tr>
          </thead>
          <tbody>
            {yearly.map((row) => (
              <tr key={row.year} className="border-b border-line/60 last:border-0">
                <th scope="row" className="py-2 pr-3 font-semibold text-ink">
                  {row.year}
                </th>
                <td className="py-2 pr-3">{row.events}</td>
                <td className="py-2 pr-3">{row.heatwave_days}</td>
                <td className="py-2 pr-3">
                  <Num value={row.avg_duration_days} unit="d" />
                </td>
                <td className="py-2 pr-3">
                  <Num value={row.avg_intensity_c} unit="°C" />
                </td>
                <td className="py-2 pr-3">
                  <Num value={row.avg_max_temp_during_events_c} unit="°C" />
                </td>
                <td className="py-2">
                  {/* Text, not just colour: a row of dots means nothing to a
                      screen reader or to someone who cannot see the tint. */}
                  {row.low_quality ? (
                    <span className="text-heat">Low quality</span>
                  ) : (
                    <span className="text-ink-muted">OK</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
