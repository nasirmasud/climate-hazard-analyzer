/**
 * Page shell, in the order the brief specifies:
 *   title -> control bar -> four summary cards -> chart + region ->
 *   insights + threshold + table -> footer
 *
 * Every panel renders from one `AnalysisResponse`. There is no second request
 * and no client-side arithmetic on results, which is what keeps the cards, the
 * chart, the insights and the table telling the same story (section 8.7).
 *
 * Until a response arrives, every number shows a skeleton or an em dash. No
 * placeholder figures appear anywhere in this file.
 *
 * The whole body is behind the backend health check. A free-tier backend that is
 * asleep cannot answer the regions call or the analysis call either, so letting
 * either one fire would mean two more requests queueing behind a boot that has
 * not started yet. Nothing below the header is rendered until it is ready, which
 * also means there is no empty dashboard sitting behind the wait, and nothing
 * focusable or clickable that cannot work.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { getRegions, ApiError } from './api'
import { CacheStatus } from './components/CacheStatus'
import { ControlBar } from './components/ControlBar'
import { DataTable } from './components/DataTable'
import { FrequencyChart } from './components/FrequencyChart'
import { InsightsPanel } from './components/InsightsPanel'
import { RegionPanel } from './components/RegionPanel'
import {
  BackendUnreachableState,
  ErrorState,
  LoadingState,
  WakingUpState,
} from './components/States'
import { SummaryCards } from './components/SummaryCards'
import { ThresholdCard } from './components/ThresholdCard'
import { TrendCard } from './components/TrendCard'
import { useAnalysis } from './hooks/useAnalysis'
import { useBackendHealth } from './hooks/useBackendHealth'
import type { AnalysisParams, PresetRegion } from './types'

function App() {
  // The gate every other request waits behind. Retry, focus and keep-alive
  // policy all live in the hook; this is only asked whether to render yet.
  const backend = useBackendHealth()
  const isReady = backend.isReady

  const regions = useQuery<PresetRegion[]>({
    queryKey: ['regions'],
    queryFn: getRegions,
    enabled: isReady,
  })

  // Only set when the form is submitted. Until then no analysis request exists,
  // so editing a year field cannot trigger a fetch.
  const [params, setParams] = useState<AnalysisParams | null>(null)
  const analysis = useAnalysis(params, isReady)

  const data = analysis.data
  const errorMessage =
    analysis.error instanceof ApiError
      ? analysis.error.message
      : analysis.error
        ? 'Something went wrong.'
        : null

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <header className="mb-6">
        <h1 className="text-3xl font-bold tracking-tight text-ink">
          Climate Hazard Trend Analyzer
        </h1>
        <p className="mt-1 text-sm text-ink-muted">
          Historical heatwave trends from Open-Meteo (ERA5) daily temperature data.
        </p>
      </header>

      {/* Announced politely rather than asserted: the wait can be thirty
          seconds, and an assertive alert would interrupt whatever the user was
          doing elsewhere on the page for a state that is expected. */}
      {!isReady && (
        <div aria-live="polite" className="mb-6">
          {backend.status === 'waking' ? (
            <WakingUpState attempt={backend.attempt} maxAttempts={backend.maxAttempts} />
          ) : (
            <BackendUnreachableState
              message={backend.errorMessage ?? 'The server did not respond.'}
              onRetry={backend.retryNow}
            />
          )}
        </div>
      )}

      {isReady && (
        <>
          <ControlBar
            regions={regions.data ?? []}
            isLoading={analysis.isFetching}
            onSubmit={setParams}
          />

          {analysis.isFetching && <LoadingState />}

          {errorMessage && (
            <ErrorState
              message={errorMessage}
              status={analysis.error instanceof ApiError ? analysis.error.status : undefined}
              onRetry={() => analysis.refetch()}
            />
          )}

          <CacheStatus data={data} isFetching={analysis.isFetching} />

          <SummaryCards data={data} isLoading={analysis.isFetching} />

          {data && (
            <>
              {/* `[&>*]:min-w-0` matters more than it looks. A grid item's automatic
                  minimum size is its content's min-content width, so an item cannot
                  shrink below what it contains. The yearly table's seven columns make
                  that width larger than a phone viewport, which forced the whole row
                  wider than the screen and took all three cards with it. Letting the
                  items shrink lets the table's own scroll container do the work it
                  was already written for. */}
              <div className="mb-4 grid gap-4 lg:grid-cols-3 [&>*]:min-w-0">
                <div className="lg:col-span-2">
                  <FrequencyChart data={data} />
                </div>
                <RegionPanel data={data} />
              </div>

              <div className="mb-4 grid gap-4 lg:grid-cols-3 [&>*]:min-w-0">
                <InsightsPanel data={data} />
                <div className="space-y-4">
                  <TrendCard data={data} />
                  <ThresholdCard data={data} />
                </div>
                <DataTable yearly={data.yearly} downloadParams={params} />
              </div>
            </>
          )}

          <footer className="mt-8 text-right text-xs text-ink-muted">
            Data source: Open-Meteo (ERA5)
          </footer>
        </>
      )}
    </main>
  )
}

export default App
