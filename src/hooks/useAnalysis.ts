/**
 * The one hook that talks to the analysis endpoint.
 *
 * `enabled` is gated on `params` being non-null, and params only becomes
 * non-null when the user presses Analyze. That is the whole point: without it
 * React Query would refetch on every keystroke in the year inputs, which is
 * both slow and rude to the backend.
 */

import { useQuery } from '@tanstack/react-query'

import { getAnalysis } from '../api'
import type { AnalysisParams, AnalysisResponse } from '../types'

export function useAnalysis(params: AnalysisParams | null) {
  return useQuery<AnalysisResponse>({
    queryKey: ['analysis', params],
    queryFn: () => getAnalysis(params as AnalysisParams),
    enabled: params !== null,
    // Analyses are stable for a given location and range, so hold onto them for
    // five minutes. Revisiting a region already seen should not refetch.
    staleTime: 5 * 60 * 1000,
    // One retry only. On a free tier the cold start can take a while, and
    // retrying a genuinely failed request just doubles the user's wait.
    retry: 1,
  })
}
