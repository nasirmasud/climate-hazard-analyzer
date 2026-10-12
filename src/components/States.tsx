/**
 * Loading and error states.
 *
 * The loading copy is deliberately short. An early version warned that a free
 * tier "may need up to a minute to wake up", but a measured analysis request
 * takes about 1.5 seconds, so promising a minute of waiting was worse than
 * saying nothing. A free-tier *service* cold start is still real and is
 * handled here separately, because that is a genuinely different wait.
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
