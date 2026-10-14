/**
 * Heatwave frequency per year, with the fitted trend line over it.
 *
 * Two decisions worth noting:
 *
 * 1. The y-domain is derived from the data, never fixed. A hard-coded 0-12 axis
 *    would flatten a quiet city's variation into a straight line at the bottom
 *    and exaggerate a busy one's.
 * 2. The x-axis is numeric, not categorical. Years are evenly spaced and
 *    should plot evenly; a category axis would treat 1990 and 1991 as
 *    unrelated and bow the trend line incorrectly.
 *
 * Low-quality years are drawn as hollow dots, so a flagged year is visible in
 * the chart rather than only in the data quality strip.
 */

import {
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { AnalysisResponse } from '../types'

interface Props {
  data: AnalysisResponse
}

/** Build chart rows from the yearly rows plus the fitted trend line. */
function buildRows(data: AnalysisResponse) {
  // The backend excludes low-quality years from the trend tests, so `fitted`
  // has one value per *included* year. Walking the yearly rows and consuming
  // `fitted` only on the good ones reproduces that alignment exactly. Indexing
  // by position instead would draw the line against the wrong years the moment
  // a year was flagged, and nothing in the response would look wrong.
  let fittedIndex = 0

  return data.yearly.map((row) => {
    const trend = row.low_quality ? null : (data.trend.fitted[fittedIndex++] ?? null)
    return {
      year: row.year,
      events: row.events,
      trend,
      lowQuality: row.low_quality,
    }
  })
}

/** Axis bounds from the data, with a little headroom so nothing clips. */
function yBounds(rows: { events: number; trend: number | null }[]) {
  const values = rows.flatMap((row) =>
    [row.events, row.trend].filter((v): v is number => v !== null),
  )
  if (values.length === 0) return { min: 0, max: 1 }

  const highest = Math.max(...values)
  const lowest = Math.min(...values)

  // Event counts cannot be negative, so the floor is always zero. Headroom
  // scales with the range so a small range is not given huge empty space.
  const headroom = Math.max((highest - lowest) * 0.15, 0.5)

  return {
    min: 0,
    max: Math.ceil(highest + headroom),
  }
}

export function FrequencyChart({ data }: Props) {
  const rows = buildRows(data)
  const { min, max } = yBounds(rows)
  const years = rows.map((row) => row.year)

  return (
    <div className="rounded-lg border border-line bg-white p-4">
      <h2 className="mb-1 text-sm font-bold text-ink">Heatwave frequency trend</h2>
      <p className="mb-3 text-xs text-ink-muted">
        Detected events per year, with the fitted trend line.
      </p>

      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#d5e7ff" />

            {/* Numeric axis: years are evenly spaced intervals, not categories. */}
            <XAxis
              dataKey="year"
              type="number"
              domain={[Math.min(...years), Math.max(...years)]}
              tickFormatter={(value: number) => String(value)}
              stroke="#5479b8"
              fontSize={12}
              label={{
                value: 'Year',
                position: 'insideBottom',
                offset: -2,
                fontSize: 12,
              }}
            />

            <YAxis
              domain={[min, max]}
              allowDecimals={false}
              stroke="#5479b8"
              fontSize={12}
              label={{
                value: 'Events',
                angle: -90,
                position: 'insideLeft',
                fontSize: 12,
              }}
            />

            <Tooltip
              formatter={(value, name) => [
                typeof value === 'number' ? value.toFixed(2) : String(value ?? ''),
                name === 'events' ? 'Events' : 'Trend line',
              ]}
              labelFormatter={(label) => String(label)}
            />

            {/* Dots at every year so a zero-event year still reads as a
                measured point rather than a gap in the line.

                A low-quality year is drawn as a hollow dot *and* excluded from
                the trend line, so the chart shows both the fact and the
                decision: the year exists, it was plotted, and it was left out
                of the fit. */}
            <Line
              type="monotone"
              dataKey="events"
              stroke="#e4492b"
              strokeWidth={2}
              dot={(props) => {
                const { cx, cy, payload } = props as {
                  cx?: number
                  cy?: number
                  payload?: { lowQuality?: boolean }
                }
                if (cx === undefined || cy === undefined) return null
                // Hollow for a low-quality year: flagged, but still plotted.
                return (
                  <circle
                    cx={cx}
                    cy={cy}
                    r={3.5}
                    fill={payload?.lowQuality ? '#ffffff' : '#e4492b'}
                    stroke="#e4492b"
                    strokeWidth={2}
                  />
                )
              }}
              activeDot={{ r: 5 }}
              isAnimationActive={false}
              name="events"
            />

            <Line
              type="linear"
              dataKey="trend"
              stroke="#1769ff"
              strokeWidth={2}
              strokeDasharray="5 4"
              dot={false}
              isAnimationActive={false}
              name="trend"
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-2 flex flex-wrap gap-4 text-xs text-ink-muted">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-5 bg-heat" aria-hidden="true" />
          Events per year
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block h-0.5 w-5"
            style={{
              backgroundImage:
                'repeating-linear-gradient(to right, #1769ff 0 5px, transparent 5px 9px)',
            }}
            aria-hidden="true"
          />
          Fitted trend
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block h-2.5 w-2.5 rounded-full border-2 border-heat bg-white"
            aria-hidden="true"
          />
          Low-quality year (excluded from trend)
        </span>
      </div>
    </div>
  )
}
