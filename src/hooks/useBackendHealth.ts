/**
 * The free-tier wake-up policy, in one place.
 *
 * The backend is a Render free web service. It spins down after roughly fifteen
 * minutes without traffic and then needs about half a minute to boot before it
 * answers anything. A plain `useQuery` gets this wrong twice: it gives up after
 * the global single retry, and it has no timeout at all, so the very first fetch
 * sits open while the instance boots and the page reads as broken rather than
 * busy.
 *
 * So this hook pins the four numbers that matter, and every one of them is
 * justified rather than guessed:
 *
 *   15s  how long one attempt may take. A booting instance holds the request
 *        open for ~30s, so an attempt that is going to succeed usually does so
 *        well inside fifteen seconds. Waiting longer would only make the first
 *        message feel broken.
 *    4s  pause between attempts. Short enough that twenty attempts still fit
 *        inside the ~80s a cold start takes, long enough not to hammer an
 *        instance that is mid-boot.
 *   20   attempts in total, which is `1 + 19 x 4s` plus the boot itself: about
 *        a minute and a half of trying before the user is told to give up.
 *   10m  keep-alive interval. Render spins down after ~15 minutes idle, so a
 *        ping well inside that window is what stops the instance sleeping under
 *        a user who is reading the page rather than clicking it.
 *
 * Three details are load-bearing:
 *
 *   - `refetchIntervalInBackground: false` is what "while the tab is visible"
 *     means. A hidden tab has no user waiting on it, and Render would let the
 *     instance sleep anyway.
 *   - `staleTime: 0` plus `refetchOnWindowFocus: 'always'` makes returning to
 *     the tab an immediate check. Without them the five-minute global default
 *     would treat a result fetched ten seconds ago as fresh and skip the ping
 *     exactly when the instance is most likely to have gone.
 *   - React Query treats an `AbortError` as a cancellation and skips the retry
 *     counter, so `api.ts` converts the timeout into a plain Error. Without
 *     that, twenty attempts would silently be one.
 */

import { useQuery } from '@tanstack/react-query'

import { API_BASE_URL, getHealth } from '../api'

/** One attempt may take this long before it is abandoned. */
export const HEALTH_TIMEOUT_MS = 15_000

/** Pause between attempts. */
export const HEALTH_RETRY_DELAY_MS = 4_000

/** Attempts in total, counting the first one. */
export const HEALTH_MAX_ATTEMPTS = 20

/** How often to re-check while the tab is visible, so the instance stays awake. */
export const HEALTH_KEEPALIVE_MS = 10 * 60 * 1000

export type BackendStatus = 'waking' | 'ready' | 'unreachable'

export interface BackendHealth {
  /** True only once the health check has succeeded at least once. */
  isReady: boolean
  status: BackendStatus
  /** Attempts made so far, 1-based, while still trying. */
  attempt: number
  maxAttempts: number
  /** A message written for the user, or null while there is nothing wrong. */
  errorMessage: string | null
  /** Restart the loop by hand after it has stopped. */
  retryNow: () => void
}

/**
 * Map a React Query result onto the three states the UI distinguishes.
 *
 * Split out from the hook so it can be tested without a DOM, and kept as a
 * function of two flags so the interesting case stays visible: React Query holds
 * on to the last successful `data` while a later keep-alive ping is retrying and
 * even after it fails. That is deliberate. A page that is already working must
 * not be thrown back behind a blocking overlay because a background ping timed
 * out, so once there is data the status stays `ready` and a failed ping is a
 * quiet event rather than a visible one.
 */
export function resolveHealthStatus(
  hasData: boolean,
  isError: boolean,
): BackendStatus {
  if (hasData) return 'ready'
  return isError ? 'unreachable' : 'waking'
}

export function useBackendHealth(): BackendHealth {
  const query = useQuery({
    queryKey: ['health'],
    queryFn: () => getHealth(HEALTH_TIMEOUT_MS),

    // React Query's `retry` counts *retries after the first attempt*, so this is
    // nineteen for twenty total attempts.
    retry: HEALTH_MAX_ATTEMPTS - 1,
    retryDelay: HEALTH_RETRY_DELAY_MS,

    // Never trust a health check from earlier in the session: the whole point is
    // to notice that the instance went away while nobody was looking.
    staleTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: 'always',

    refetchInterval: HEALTH_KEEPALIVE_MS,
    refetchIntervalInBackground: false,
  })

  const status = resolveHealthStatus(query.data !== undefined, query.isError)

  return {
    isReady: status === 'ready',
    status,
    // `failureCount` is retries so far, so the first attempt reads as one.
    attempt: Math.min(query.failureCount + 1, HEALTH_MAX_ATTEMPTS),
    maxAttempts: HEALTH_MAX_ATTEMPTS,
    errorMessage:
      status === 'unreachable'
        ? `Could not reach the server at ${API_BASE_URL}. It may have been put to sleep, or the address may be wrong.`
        : null,
    retryNow: () => {
      void query.refetch()
    },
  }
}