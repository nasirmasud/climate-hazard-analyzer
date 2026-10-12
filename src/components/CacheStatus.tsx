/**
 * Says whether the numbers on screen were just computed or came from the cache.
 *
 * This is a small strip rather than a line buried in the region panel,
 * because it changes how a reader should treat everything else on the page.
 * A freshly computed result is the first time this location has been analysed;
 * a cached one is a stored answer, which is the same numbers but not a new
 * reading of the weather service.
 *
 * Three states, matching what React Query actually knows:
 *
 * - no data yet: nothing is claimed, because nothing has arrived
 * - a fetch in flight over existing data: the previous result is still on
 *   screen, so it is described as being refreshed rather than discarded
 * - data present: the server's own `cached` flag, verbatim
 *
 * The flag is read straight off the response and never inferred from timing.
 * Guessing from elapsed milliseconds would be a lie: a cached hit and a cold
 * hit on a fast connection can take the same time.
 */

import type { AnalysisResponse } from '../types'

interface Props {
  data: AnalysisResponse | undefined
  isFetching: boolean
}

export function CacheStatus({ data, isFetching }: Props) {
  if (!data) {
    return null
  }

  // Existing results are on screen and a new request is in flight. Say so,
  // rather than showing a flag that describes the outgoing request.
  if (isFetching) {
    return (
      <p className="mb-3 text-xs text-ink-muted" role="status" aria-live="polite">
        Refreshing these results…
      </p>
    )
  }

  const cached = data.cached

  return (
    <p
      className="mb-3 flex items-center gap-2 text-xs text-ink-muted"
      role="status"
      aria-live="polite"
    >
      <span
        aria-hidden="true"
        className={`inline-block h-2 w-2 rounded-full ${cached ? 'bg-trend' : 'bg-heat'}`}
      />
      {cached ? (
        <span>Served from cache — these results were stored by an earlier request.</span>
      ) : (
        <span>
          Computed just now — this is the first analysis of this location and range.
        </span>
      )}
    </p>
  )
}
