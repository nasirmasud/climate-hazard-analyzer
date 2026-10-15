/**
 * The four summary cards.
 *
 * Every figure comes from the response and nothing is hard-coded. While the
 * request is in flight each card shows a skeleton rather than a zero or a
 * dash, because a zero would read as a real measurement of "no heatwaves". A
 * genuine null from the API renders as an em dash, the agreed signal for "we
 * do not know".
 */

import type { AnalysisResponse } from '../types'

/** Renders an em dash for null, so an unknown value never looks like a zero. */
function Value({ value, unit }: { value: number | null; unit?: string }) {
  if (value === null) {
    return <span className="text-ink-muted">—</span>
  }
  return (
    <>
      {value.toFixed(2)}
      {unit && <span className="ml-1 text-sm font-normal text-ink-muted">{unit}</span>}
    </>
  )
}

function Skeleton() {
  return <div className="h-7 w-20 animate-pulse rounded bg-line" />
}

function Card({
  label,
  note,
  children,
}: {
  label: string
  note: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-line bg-white p-4">
      <p className="text-xs font-semibold text-ink-muted">{label}</p>
      <p className="mt-1 text-2xl font-bold text-ink">{children}</p>
      <p className="mt-1 text-xs text-ink-muted">{note}</p>
    </div>
  )
}

interface Props {
  data: AnalysisResponse | undefined
  isLoading: boolean
}

export function SummaryCards({ data, isLoading }: Props) {
  const summary = data?.summary
  // One flag drives every card, so they cannot disagree about whether a
  // response has arrived.
  const pending = isLoading || !summary

  return (
    <div className="mb-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Card
        label="Total events"
        note={
          summary
            ? `${summary.events_per_year.toFixed(2)} per year`
            : 'Across the selected range'
        }
      >
        {pending ? <Skeleton /> : summary.total_events}
      </Card>

      <Card label="Average duration" note="Days per event">
        {pending ? <Skeleton /> : <Value value={summary.avg_duration_days} unit="days" />}
      </Card>

      <Card label="Average intensity" note="Degrees above the day threshold">
        {pending ? <Skeleton /> : <Value value={summary.avg_intensity_c} unit="°C" />}
      </Card>

      <Card label="Peak average" note="Mean daily max during heatwave days">
        {pending ? (
          <Skeleton />
        ) : (
          <Value value={summary.avg_max_temp_during_events_c} unit="°C" />
        )}
      </Card>
    </div>
  )
}
