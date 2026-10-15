/**
 * Loading and error states.
 *
 * Two different waits live here and must not be confused. "Analyzing" is a
 * computed result and takes about 1.5 seconds — promising a minute for
 * something that lands in a second and a half teaches the user that every
 * message here is padding. "Server is starting up" is the free-tier *service*
 * cold start, real and measured at roughly thirty seconds, which is genuinely
 * something to wait on and gets its own component because the user cannot make
 * it go faster and silence would read as a hang.
 */

export function LoadingState() {
  return (
    <div
      className="mb-4 rounded-lg border border-line bg-white p-4"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-3">
        <div
          className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-heat"
          aria-hidden="true"
        />
        <p className="text-sm text-ink">
          Analyzing. The first request for a location fetches its full daily history, so
          it can take a few seconds.
        </p>
      </div>
    </div>
  )
}

interface WakingUpProps {
  /** Attempts made so far, 1-based. */
  attempt: number
  maxAttempts: number
}

/**
 * Shown while the backend is booting, from the very first attempt.
 *
 * The counter is honest rather than reassuring: twenty attempts four seconds
 * apart really is about a minute and a half, and "1 of 20" on a dead address
 * tells the user far more than an unbounded spinner would.
 */
export function WakingUpState({ attempt, maxAttempts }: WakingUpProps) {
  return (
    <div
      className="rounded-lg border border-line bg-white p-6 text-center"
      role="status"
      aria-live="polite"
    >
      <div className="flex justify-center">
        <div
          className="h-6 w-6 animate-spin rounded-full border-2 border-line border-t-heat"
          aria-hidden="true"
        />
      </div>

      <p className="mt-4 text-sm font-semibold text-ink">Server is starting up</p>

      <p className="mx-auto mt-2 max-w-sm text-xs text-ink-muted">
        The server has been idle, so this first load has to wait for it to start.
        That takes about 30–35 seconds. Loads after this one are fast.
      </p>

      <p className="mt-3 text-xs text-ink-muted">
        Attempt {attempt} of {maxAttempts}. This page will carry on by itself as soon as
        the server is ready — no need to reload.
      </p>
    </div>
  )
}

interface BackendDownProps {
  message: string
  onRetry: () => void
}

/**
 * Shown once every attempt has been used up.
 *
 * The button exists because the loop has genuinely stopped, so waiting does
 * nothing more. It restarts a full twenty attempts rather than one, because the
 * likeliest cause is still a booting instance that outran the budget.
 */
export function BackendUnreachableState({ message, onRetry }: BackendDownProps) {
  return (
    <div
      className="rounded-lg border border-heat bg-heat-soft p-6 text-center"
      role="alert"
    >
      <p className="text-sm font-semibold text-heat">Could not reach the server</p>
      <p className="mx-auto mt-2 max-w-sm text-xs text-ink">{message}</p>

      <button
        type="button"
        onClick={onRetry}
        className="mt-4 rounded border border-heat px-3 py-1.5 text-xs font-semibold text-heat"
      >
        Try again
      </button>

      <p className="mt-3 text-xs text-ink-muted">
        Retrying keeps checking for about another minute.
      </p>
    </div>
  )
}

/**
 * Shown when the very first request fails, which on a free tier usually means
 * the service is still starting rather than that anything is wrong.
 */
export function ColdStartHint() {
  return (
    <p className="mt-2 rounded border border-line bg-surface px-3 py-2 text-xs text-ink-muted">
      If this is the first request, the server may need up to a minute to wake up. Wait a
      moment and try again.
    </p>
  )
}

interface ErrorProps {
  message: string
  status?: number
  onRetry: () => void
}

export function ErrorState({ message, status, onRetry }: ErrorProps) {
  // 502 means the upstream weather service failed, not that the request was
  // wrong. The copy differs because the user's next action differs.
  const isUpstream = status === 502

  return (
    <div className="mb-4 rounded-lg border border-heat bg-heat-soft p-4" role="alert">
      <p className="text-sm font-semibold text-heat">
        {isUpstream ? 'Weather data service unavailable' : 'Could not run the analysis'}
      </p>
      <p className="mt-1 text-xs text-ink">{message}</p>

      {isUpstream ? (
        <ColdStartHint />
      ) : (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded border border-heat px-3 py-1.5 text-xs font-semibold text-heat"
        >
          Try again
        </button>
      )}
    </div>
  )
}
