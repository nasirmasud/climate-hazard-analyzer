/**
 * Page shell for the layout the brief specifies, top to bottom:
 *   title -> control bar -> four summary cards -> chart + region ->
 *   insights + threshold + table -> footer
 *
 * Phase 1 is the contract phase, so the panels are empty placeholders. Every
 * one shows an em dash rather than a made-up number, because nothing here may
 * display a value that did not come from the API.
 */

import { useQuery } from '@tanstack/react-query'

import { getHealth, getRegions } from './api'
import type { PresetRegion } from './types'

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-white p-4">
      <h2 className="mb-3 text-sm font-bold text-ink">{title}</h2>
      {children}
    </section>
  )
}

function Placeholder({ children }: { children: string }) {
  return <p className="text-xs text-ink-muted">{children}</p>
}

function App() {
  // Confirms the backend is reachable. Also doubles as a cold-start check:
  // on a free tier the first request can take about a minute.
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth })

  const regions = useQuery<PresetRegion[]>({
    queryKey: ['regions'],
    queryFn: getRegions,
  })

  const backendLabel = health.isPending
    ? 'checking backend…'
    : health.isError
      ? 'backend unreachable'
      : `backend ok · ${regions.data?.length ?? 0} preset regions`

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <header className="mb-6">
        <h1 className="text-3xl font-bold tracking-tight text-ink">
          Climate Hazard Trend Analyzer
        </h1>
        <p className="mt-1 text-sm text-ink-muted">
          Historical heatwave trends from Open-Meteo (ERA5) daily temperature data.
        </p>
        <p className="mt-2 text-xs text-ink-muted">{backendLabel}</p>
      </header>

      <div className="mb-4 rounded-lg border border-line bg-white p-4">
        <p className="text-sm text-ink-muted">
          Control bar, summary cards and results panels arrive in Phase 2.
        </p>
      </div>

      <div className="mb-4 grid gap-4 md:grid-cols-2">
        <Panel title="Summary">
          <Placeholder>—</Placeholder>
        </Panel>
        <Panel title="Heatwave frequency trend">
          <Placeholder>—</Placeholder>
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Key insights">
          <Placeholder>—</Placeholder>
        </Panel>
        <Panel title="Threshold">
          <Placeholder>—</Placeholder>
        </Panel>
        <Panel title="Detailed data">
          <Placeholder>—</Placeholder>
        </Panel>
      </div>

      <footer className="mt-8 text-right text-xs text-ink-muted">
        Data source: Open-Meteo (ERA5)
      </footer>
    </main>
  )
}

export default App
