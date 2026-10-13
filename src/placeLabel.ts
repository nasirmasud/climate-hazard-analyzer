/**
 * Label rules for the custom-area option in the Location dropdown.
 *
 * Split out from the component so the rules can be tested without a DOM, and so
 * the invariants are stated once rather than implied by JSX:
 *
 * - a missing, blank or failed lookup keeps the plain fallback label
 * - a label is never coordinates
 * - a name is shortened and tidied, never truncated mid-word into nonsense
 *
 * The fallback label is deliberately fixed text. It is not built from whatever
 * happened to be selected, because "Custom area" is the honest description of a
 * box with no name attached, and anything else would be claiming to know
 * something we do not.
 */

export const PLACE_FALLBACK_LABEL = 'Custom area (enter a box)'

/** Longest name kept in the dropdown before it is trimmed. */
const MAX_LABEL_LENGTH = 40

/** Fields that would be coordinates rather than a place name. */
const COORDINATE_FIELDS = ['lat', 'lon', 'latitude', 'longitude']

/** Matches a bare decimal pair such as "23.81, 90.41". */
const COORDINATE_PAIR = /^-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?$/

/**
 * True when a string is really a coordinate pair rather than a place name.
 *
 * Guards against a future change passing coordinates through, and against an
 * upstream service that decides to echo its query back. The rule is that a label
 * never contains lat/lon, so the check is explicit rather than implied by where
 * the value came from.
 */
export function looksLikeCoordinates(value: string): boolean {
  const haystack = value.trim().toLowerCase()

  for (const field of COORDINATE_FIELDS) {
    if (haystack.includes(`${field}=`) || haystack.includes(`${field}:`)) {
      return true
    }
  }

  return COORDINATE_PAIR.test(haystack)
}

/** Trim a raw upstream name into something a dropdown can show. */
export function sanitisePlaceName(raw: string | null | undefined): string | null {
  if (typeof raw !== 'string') return null

  // Collapse runs of whitespace, including newlines from a wrapped name.
  const cleaned = raw.replace(/\s+/g, ' ').trim()
  if (!cleaned) return null

  if (looksLikeCoordinates(cleaned)) return null

  if (cleaned.length <= MAX_LABEL_LENGTH) return cleaned

  // Trim on a word boundary so a long name is shortened rather than cut.
  const clipped = cleaned.slice(0, MAX_LABEL_LENGTH)
  const lastSpace = clipped.lastIndexOf(' ')

  // Only respect the boundary if most of the name survives, otherwise a single
  // very long word would be truncated to nothing useful.
  if (lastSpace > MAX_LABEL_LENGTH / 2) {
    return `${clipped.slice(0, lastSpace).trimEnd()}…`
  }

  return `${clipped.trimEnd()}…`
}

/**
 * The label to show for the custom-area option.
 *
 * Any failure — no name, an empty string, a network error, a slow lookup that
 * was abandoned — resolves to the fallback. There is no path through this
 * function that produces an empty label, because an empty dropdown entry looks
 * like a rendering bug.
 */
export function resolvePlaceLabel(
  name: string | null | undefined,
  failed = false,
): string {
  if (failed) return PLACE_FALLBACK_LABEL
  return sanitisePlaceName(name) ?? PLACE_FALLBACK_LABEL
}
