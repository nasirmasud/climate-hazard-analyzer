/**
 * Location and year pickers.
 *
 * This component holds the values the user is editing. It does *not* fetch.
 * Analysis only runs when the form is submitted, so a half-typed coordinate or
 * year never reaches the API. `onSubmit` receives a params object and nothing
 * else runs until then.
 *
 * Two location modes, matching the backend's request model exactly:
 *
 * - a preset, which sends `region=<id>`
 * - a custom box, which sends the four min/max values
 *
 * **Why the center point is shown.** ERA5 is a grid. A box is not averaged
 * across its area; the backend queries the single cell at the box's centre.
 * A user who types a wide box and sees "analysing that region" would reasonably
 * assume the whole area was covered. Displaying the centre point makes the
 * actual behaviour visible instead of merely documented.
 *
 * Every field has a visible label and every control is keyboard reachable.
 * Validation mirrors the backend's rules (section 11.1) so an obviously invalid
 * input is caught before a request goes out, but the backend still validates:
 * client-side checks are a convenience, never the authority.
 */

import { useEffect, useId, useMemo, useRef, useState } from 'react'

import { boxCenter, clampBox } from '../boxGeometry'
import { lookupPlace } from '../placeLookup'
import type { PlaceLookup } from '../placeLookup'
import { PLACE_FALLBACK_LABEL, resolvePlaceLabel } from '../placeLabel'
import type { BoxBounds } from '../validation'
import {
  EARLIEST_YEAR,
  MAX_BOX_SPAN_DEG,
  MIN_YEAR_SPAN,
  lastCompleteYear,
  round2,
  validateBox,
  validateRange,
} from '../validation'
import { RegionMap } from './RegionMap'
import type { AnalysisParams, PresetRegion } from '../types'

interface Props {
  regions: PresetRegion[]
  isLoading: boolean
  onSubmit: (params: AnalysisParams) => void
}

type Mode = 'preset' | 'box'

const CUSTOM_OPTION = '__custom__'

/**
 * Parse one box bound. Blank is not zero: an empty field must not silently
 * become a coordinate off the coast of Africa.
 */
function parseBound(raw: string): number | null {
  if (raw.trim() === '') return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

export function ControlBar({ regions, isLoading, onSubmit }: Props) {
  const lastYear = lastCompleteYear()
  const ids = {
    region: useId(),
    hazard: useId(),
    start: useId(),
    end: useId(),
    minLat: useId(),
    maxLat: useId(),
    minLon: useId(),
    maxLon: useId(),
  }

  const [region, setRegion] = useState(regions[0]?.id ?? 'dhaka')
  const [mode, setMode] = useState<Mode>('preset')
  const [startYear, setStartYear] = useState(EARLIEST_YEAR)
  const [endYear, setEndYear] = useState(lastYear)

  // Blank strings rather than 0: an empty field should not read as a
  // coordinate of zero off the coast of Africa.
  const [minLat, setMinLat] = useState('')
  const [maxLat, setMaxLat] = useState('')
  const [minLon, setMinLon] = useState('')
  const [maxLon, setMaxLon] = useState('')

  const [error, setError] = useState<string | null>(null)

  /**
   * The custom-area option's label.
   *
   * Starts as, and falls back to, the plain text. Only a successful lookup ever
   * changes it, so the default is always available.
   */
  const [customLabel, setCustomLabel] = useState(PLACE_FALLBACK_LABEL)

  // The in-flight lookup, so a new click can abandon it.
  const lookupRef = useRef<PlaceLookup | null>(null)

  // Monotonic token. A response is applied only if its token is still current,
  // which is what stops a slow earlier click from overwriting a later one.
  const nameTokenRef = useRef(0)

  /**
   * The one box value, shared by the inputs and the map.
   *
   * `null` means "the four inputs are not yet a complete box". Typing a single
   * digit is a legitimate intermediate state, and collapsing it to a marker
   * would make the map jump around while the user is still typing.
   */
  const box = useMemo<BoxBounds | null>(() => {
    const values = [minLat, maxLat, minLon, maxLon].map(parseBound)
    if (values.some((value) => value === null)) return null
    const [nla, xla, nlo, xlo] = values as number[]
    if (nla >= xla || nlo >= xlo) return null
    return { min_lat: nla, max_lat: xla, min_lon: nlo, max_lon: xlo }
  }, [minLat, maxLat, minLon, maxLon])

  // The centre point, shown live while the user types. Null until all four
  // bounds parse, because a centre computed from a half-typed box is a lie.
  const center = useMemo(() => {
    if (!box) return null
    return {
      lat: round2((box.min_lat + box.max_lat) / 2),
      lon: round2((box.min_lon + box.max_lon) / 2),
    }
  }, [box])

  /**
   * A click on the map writes the inputs, so both views stay in step.
   *
   * The coordinates are set synchronously and never wait on the name lookup. The
   * lookup runs afterwards and only applies if it is still the newest one, so a
   * slow response cannot overwrite a label from a more recent click.
   */
  function handleMapPick(picked: BoxBounds) {
    const next = clampBox(picked)
    setMinLat(String(next.min_lat))
    setMaxLat(String(next.max_lat))
    setMinLon(String(next.min_lon))
    setMaxLon(String(next.max_lon))
    setError(null)

    // Abandon whatever the previous click started.
    lookupRef.current?.cancel()
    lookupRef.current = null

    const centre = boxCenter(next)
    if (!centre) return

    const token = ++nameTokenRef.current
    const lookup = lookupPlace(centre[0], centre[1])
    lookupRef.current = lookup

    lookup.promise.then((name) => {
      // Discarded if the user has clicked again, or the mode has changed.
      if (token !== nameTokenRef.current) return
      setCustomLabel(resolvePlaceLabel(name))
    })
  }

  /** Switching location mode invalidates any in-flight name lookup. */
  function handleModeChange(next: Mode) {
    lookupRef.current?.cancel()
    lookupRef.current = null
    nameTokenRef.current++
    setCustomLabel(PLACE_FALLBACK_LABEL)
    setMode(next)
  }

  /** Choosing a preset clears any custom box, so the two cannot disagree. */
  function handleRegionChange(value: string) {
    if (value === CUSTOM_OPTION) {
      handleModeChange('box')
    } else {
      handleModeChange('preset')
      setRegion(value)
    }
    setError(null)
  }

  // Never leave a request running after the form is gone.
  useEffect(() => {
    return () => {
      lookupRef.current?.cancel()
    }
  }, [])

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()

    const rangeProblem = validateRange(startYear, endYear, lastYear)
    if (rangeProblem) {
      setError(rangeProblem)
      return
    }

    if (mode === 'box') {
      // parseBound returns null for a blank field, which is a different problem
      // from an out-of-range one and deserves a different message.
      const bounds = {
        minLat: parseBound(minLat),
        maxLat: parseBound(maxLat),
        minLon: parseBound(minLon),
        maxLon: parseBound(maxLon),
      }
      if (Object.values(bounds).some((value) => value === null)) {
        setError('Enter all four bounds of the area, or click the map.')
        return
      }

      const candidate = {
        min_lat: bounds.minLat as number,
        max_lat: bounds.maxLat as number,
        min_lon: bounds.minLon as number,
        max_lon: bounds.maxLon as number,
      }

      const boxProblem = validateBox(candidate)
      if (boxProblem) {
        setError(boxProblem)
        return
      }

      setError(null)
      // Rounded before sending, so a box typed to six decimal places shares a
      // cache entry with one typed to two. Without this, every variation of
      // the same box would be its own cache row and its own upstream fetch.
      onSubmit({
        min_lat: round2(candidate.min_lat),
        max_lat: round2(candidate.max_lat),
        min_lon: round2(candidate.min_lon),
        max_lon: round2(candidate.max_lon),
        start_year: startYear,
        end_year: endYear,
        hazard: 'heatwave',
      })
      return
    }

    setError(null)
    onSubmit({
      region,
      start_year: startYear,
      end_year: endYear,
      hazard: 'heatwave',
    })
  }

  const numberClass = 'w-full rounded border border-line bg-white px-3 py-2 text-sm'

  return (
    <form
      onSubmit={handleSubmit}
      // Hidden when printing: inputs, the preset picker and the Analyze button
      // are controls, and controls are meaningless on paper. Everything they
      // produced is in the results below.
      className="mb-4 rounded-lg border border-line bg-white p-4 print:hidden"
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label htmlFor={ids.region} className="mb-1 block text-xs font-semibold">
            Location
          </label>
          <select
            id={ids.region}
            value={mode === 'box' ? CUSTOM_OPTION : region}
            onChange={(e) => handleRegionChange(e.target.value)}
            className={numberClass}
          >
            {regions.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.name}
              </option>
            ))}
            <option value={CUSTOM_OPTION}>{customLabel}</option>
          </select>
        </div>

        <div>
          <label htmlFor={ids.hazard} className="mb-1 block text-xs font-semibold">
            Hazard
          </label>
          {/* Heatwave is the only hazard the backend accepts. It is a select
              rather than a plain label so the control shape matches the
              location picker and the choice is explicit. */}
          <select id={ids.hazard} value="heatwave" className={numberClass}>
            <option value="heatwave">Heatwave</option>
          </select>
        </div>

        <div>
          <label htmlFor={ids.start} className="mb-1 block text-xs font-semibold">
            Start year
          </label>
          {/*
            `step={1}`, unlike the coordinate inputs above which use
            `step="any"`. Years really are whole numbers and the code only ever
            produces whole numbers, so an integer step cannot reject anything we
            set. Stating it explicitly stops a later reader from making these
            consistent with the coordinates and quietly allowing 1995.5.
          */}
          <input
            id={ids.start}
            type="number"
            step={1}
            min={EARLIEST_YEAR}
            max={lastYear}
            value={startYear}
            onChange={(e) => setStartYear(Number(e.target.value))}
            className={numberClass}
          />
        </div>

        <div>
          <label htmlFor={ids.end} className="mb-1 block text-xs font-semibold">
            End year
          </label>
          <input
            id={ids.end}
            type="number"
            step={1}
            min={EARLIEST_YEAR}
            max={lastYear}
            value={endYear}
            onChange={(e) => setEndYear(Number(e.target.value))}
            className={numberClass}
          />
        </div>
      </div>

      {mode === 'box' && (
        <fieldset className="mt-4 border-t border-line pt-4">
          <legend className="mb-2 text-xs font-semibold">
            Area bounds (maximum {MAX_BOX_SPAN_DEG}° on each side)
          </legend>

          {/*
            `step="any"`, not `step="0.01"`.

            A map click produces bounds with three decimals — the 0.375 degree
            half-span puts them on a third decimal, and that precision is what
            keeps the centre exactly on the clicked point. A fixed 0.01 step made
            the browser reject those values as a step mismatch and show
            "Please enter a valid value" on every click, which read as the map
            being broken rather than as a rounding conflict.

            Our own validation still covers what matters: the coordinate ranges,
            the 2 degree limit, and minimum being less than maximum. Those rules
            produce messages written for a person; the native bubble is generic
            and says nothing about which rule was broken.
          */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <label htmlFor={ids.minLat} className="mb-1 block text-xs">
                Min latitude
              </label>
              <input
                id={ids.minLat}
                type="number"
                step="any"
                min={-90}
                max={90}
                value={minLat}
                onChange={(e) => setMinLat(e.target.value)}
                className={numberClass}
              />
            </div>
            <div>
              <label htmlFor={ids.maxLat} className="mb-1 block text-xs">
                Max latitude
              </label>
              <input
                id={ids.maxLat}
                type="number"
                step="any"
                min={-90}
                max={90}
                value={maxLat}
                onChange={(e) => setMaxLat(e.target.value)}
                className={numberClass}
              />
            </div>
            <div>
              <label htmlFor={ids.minLon} className="mb-1 block text-xs">
                Min longitude
              </label>
              <input
                id={ids.minLon}
                type="number"
                step="any"
                min={-180}
                max={180}
                value={minLon}
                onChange={(e) => setMinLon(e.target.value)}
                className={numberClass}
              />
            </div>
            <div>
              <label htmlFor={ids.maxLon} className="mb-1 block text-xs">
                Max longitude
              </label>
              <input
                id={ids.maxLon}
                type="number"
                step="any"
                min={-180}
                max={180}
                value={maxLon}
                onChange={(e) => setMaxLon(e.target.value)}
                className={numberClass}
              />
            </div>
          </div>

          {/* The map and the inputs are two views of one value. Clicking here
              rewrites the four fields above; typing there moves this marker. */}
          <RegionMap box={box} onPick={handleMapPick} />

          <p className="mt-2 text-xs text-ink-muted">
            Click the map to choose a point. It sets an area square on the map, with the
            latitude span kept at one degree and the longitude span widened to match, so
            it stays inside the {MAX_BOX_SPAN_DEG}° limit. The four bounds above fill in
            automatically. The rectangle is the area you selected; the marker is the
            single point that is actually queried.
          </p>

          {/* Says out loud what will actually be queried. ERA5 is a grid, so
              only this one cell is read. */}
          <p className="mt-2 text-xs text-ink-muted">
            {center ? (
              <>
                Centre point queried:{' '}
                <span className="font-semibold text-ink">
                  {center.lat.toFixed(2)}, {center.lon.toFixed(2)}
                </span>
                . Data is a gridded estimate, so only the cell at this point is used, not
                the whole area.
              </>
            ) : (
              'Enter four bounds, or click the map, to see the centre point that will be queried.'
            )}
          </p>
        </fieldset>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={isLoading}
          className="rounded bg-heat px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isLoading ? 'Analyzing…' : 'Analyze'}
        </button>
        <span className="text-xs text-ink-muted">
          {EARLIEST_YEAR}–{lastYear} available · minimum {MIN_YEAR_SPAN} years
        </span>
      </div>

      {/* role="alert" so a screen reader announces the problem as soon as it
          appears, rather than leaving it to be found. Inline, never alert(). */}
      {error && (
        <p
          role="alert"
          className="mt-3 rounded border border-heat bg-heat-soft px-3 py-2 text-xs text-heat"
        >
          {error}
        </p>
      )}
    </form>
  )
}
