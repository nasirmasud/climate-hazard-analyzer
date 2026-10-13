/**
 * Location picker on an OpenStreetMap base.
 *
 * Clicking anywhere on the map sets a box centred on that point. The latitude
 * half-span is half a degree; the longitude half-span is widened by
 * 0.5 / cos(latitude) so the box looks square on the map rather than square in
 * degrees. Both spans stay well inside the 2 degree limit the brief imposes, so
 * a click can never produce a request the backend rejects.
 *
 * The centre is the click itself at every latitude, so the squareness
 * correction never moves the point that gets queried.
 *
 * The map and the lat/lon inputs are two views of one value. Neither owns the
 * state: `ControlBar` does, so typing in an input moves the marker and clicking
 * the map rewrites the inputs. That is what keeps them in sync, rather than
 * syncing them after the fact and hoping.
 *
 * ERA5 is a grid, so only the point at the box's centre is actually queried.
 * The rectangle shows the area the user selected and the marker sits at the one
 * point that is read, which keeps that distinction visible instead of implied.
 */

import { useEffect, useMemo } from 'react'
import {
  MapContainer,
  Marker,
  Rectangle,
  TileLayer,
  useMap,
  useMapEvents,
} from 'react-leaflet'
import { divIcon } from 'leaflet'

import { boxCenter, boxFromClick } from '../boxGeometry'
import type { BoxBounds } from '../validation'

const OSM_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

/** Somewhere sensible to look before anything has been chosen. */
const DEFAULT_CENTER: [number, number] = [23.8, 90.4]

/**
 * Zoom for the initial view. 6 shows South Asia — roughly Bangladesh, Nepal and
 * most of eastern India — which is where every preset region sits, so the map
 * opens somewhere relevant rather than on the whole planet.
 */
const DEFAULT_ZOOM = 6

/** The zoom to use once a box has been chosen: close enough to place a point. */
const CHOSEN_ZOOM = 8

/**
 * Height of the map, in pixels. A fixed value, never `auto` or a percentage.
 *
 * Leaflet measures its container to fill it, so a parent that sizes itself from
 * its content collapses the map to zero before that measurement can happen. A
 * number here is the only thing that reliably works.
 */
const MAP_HEIGHT_PX = 360

interface Props {
  box: BoxBounds | null
  onPick: (box: BoxBounds) => void
}

/**
 * A plain div marker rather than Leaflet's default pin image.
 *
 * The default pins come from image files that a bundler does not rewrite inside
 * a package, which produces the classic "marker-icon.png failed to load"
 * problem. A divIcon needs no asset and reuses the warm accent from the theme.
 */
/**
 * A plain div marker rather than Leaflet's default pin image.
 *
 * The default pins come from image files that a bundler does not rewrite inside
 * a package, which produces the classic "marker-icon.png failed to load"
 * problem. A divIcon needs no asset and reuses the warm accent from the theme.
 *
 * **No negative margin on the inner div.** `iconAnchor: [7, 7]` already centres
 * the 14x14 icon box on the coordinate. An earlier version also set
 * `margin: -7px 0 0 -7px` on the inner div, which offset it a *second* time and
 * put the dot seven pixels up and to the left of the rectangle's true centre.
 */
const centreIcon = divIcon({
  className: '',
  html: '<div style="width:14px;height:14px;border-radius:50%;background:#e4492b;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.4)"></div>',
  iconSize: [14, 14],
  iconAnchor: [7, 7],
})

/**
 * The map is a control, so it needs a name and a tab stop.
 *
 * Leaflet's container is a plain div with no role, which leaves it invisible to
 * a screen reader and unreachable by keyboard. `tabIndex` puts it in the tab
 * order; once focused, the arrow keys pan and plus and minus zoom.
 */
const MAP_PROPS = {
  role: 'application',
  'aria-label':
    'Location map. Click to set an area, or use the arrow keys to pan and plus or minus to zoom.',
  tabIndex: 0,
} as const

/**
 * Turns map clicks into a new box. A child so it can use the map context.
 *
 * Mounted unconditionally. This component is what creates the *first* box, so
 * gating it on an existing box would be circular: no box means no click
 * handler, and without a click handler there can never be a box.
 */
function ClickHandler({ onPick }: { onPick: Props['onPick'] }) {
  useMapEvents({
    click(event) {
      onPick(boxFromClick(event.latlng.lat, event.latlng.lng))
    },
  })
  return null
}

/** Keeps the viewport on the centre when it changes, without changing zoom. */
function Recentre({ center }: { center: [number, number] }) {
  const map = useMap()
  useEffect(() => {
    map.setView(center, map.getZoom())
  }, [center, map])
  return null
}

export function RegionMap({ box, onPick }: Props) {
  const center = useMemo(() => boxCenter(box), [box])

  return (
    <div className="mt-3 overflow-hidden rounded border border-line">
      <MapContainer
        center={center ?? DEFAULT_CENTER}
        zoom={center ? CHOSEN_ZOOM : DEFAULT_ZOOM}
        // A fixed height, because a Leaflet map inside an auto-height parent
        // collapses to zero before it can measure itself.
        style={{ height: MAP_HEIGHT_PX, width: '100%' }}
        scrollWheelZoom
        // Below the sticky headers, so panning never scrolls the page instead.
        className="z-0"
        {...MAP_PROPS}
      >
        <TileLayer url={OSM_URL} attribution={OSM_ATTRIBUTION} />

        {/* Always mounted: this is what creates the first box. */}
        <ClickHandler onPick={onPick} />

        {center && <Recentre center={center} />}

        {box && center && (
          <>
            {/* The area the user selected... */}
            <Rectangle
              bounds={[
                [box.min_lat, box.min_lon],
                [box.max_lat, box.max_lon],
              ]}
              pathOptions={{
                color: '#e4492b',
                weight: 1.5,
                fillOpacity: 0.08,
              }}
            />
            {/* ...and the one point that is actually queried. */}
            <Marker position={center} icon={centreIcon} />
          </>
        )}
      </MapContainer>
    </div>
  )
}
