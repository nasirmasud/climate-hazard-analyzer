/**
 * The one hook that talks to the analysis endpoint.
 *
 * `enabled` is gated on two things, and both are load-bearing:
 *
 *   `params !== null`  params only becomes non-null when the user presses
 *                      Analyze. Without this, React Query would refetch on every
 *                      keystroke in the year inputs, which is both slow and rude
 *                      to the backend.
 *
 *   `isReady`          the backend health check has succeeded. If the instance is
 *                      asleep, submitting a form during the ~30 second boot
 *                      would queue an analysis behind a request that has not been
 *                      answered yet. Gating means the request is issued the
 *                      moment the health check passes, so a user who submits
 *                      during the wait gets their analysis without pressing
 *                      Analyze a second time.
 */

import { useQuery } from '@tanstack/react-query'

import { getAnalysis } from '../api'
import type { AnalysisParams, AnalysisResponse } from '../types'

export function useAnalysis(params: AnalysisParams | null, isReady = true) {
  return useQuery<AnalysisResponse>({
    queryKey: ['analysis', params],
    queryFn: () => getAnalysis(params as AnalysisParams),
    enabled: isReady && params !== null,
    // Analyses are stable for a given location and range, so hold onto them for
    // five minutes. Revisiting a region already seen should not refetch.
    staleTime: 5 * 60 * 1000,
    // One retry only. The wake-up loop has already proved the backend is up by
    // the time this runs, so a failure here is the request itself, not the
    // service, and retrying a genuinely failed request doubles the user's wait.
    retry: 1,
  })
}