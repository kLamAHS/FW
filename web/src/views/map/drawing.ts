/**
 * Drawing on your own map (§66).
 *
 * The principle the entire generator is built around — `_authored_outlines` refuses to
 * redraw a region the writer outlined, the coastline grows to fit their borders, their
 * castles are pinned where they put them — and until now nothing in the application
 * could make such a shape. The map honoured a drawing nobody could draw.
 *
 * Screen to world goes through the SVG's own `getScreenCTM()` on the transformed group,
 * which already accounts for the viewBox, `preserveAspectRatio` and the pan/zoom
 * transform. Deriving it by hand from the pan state would be a second, subtly different
 * answer to a question the browser answers exactly.
 */

import type { Entity } from '../../api'

/** What a writer draws, in their words — the layer and shape follow from the choice. */
export const DRAWABLE = [
  { key: 'border', label: 'a border', kind: 'polygon', layer: 'regions',
    hint: 'Click each corner; the shape closes itself.', role: 'border' },
  { key: 'road', label: 'a road', kind: 'line', layer: 'roads',
    hint: 'Click along the way it runs.', role: 'road' },
  { key: 'river', label: 'a river', kind: 'line', layer: 'waterways',
    hint: 'Click from its head down to its mouth.', role: 'waterway' },
  { key: 'place', label: 'a place', kind: 'point', layer: 'settlements',
    hint: 'Click where it stands.', role: 'settlement' },
] as const

export type Drawable = (typeof DRAWABLE)[number]

export interface Drawing {
  what: Drawable
  entity: Entity
  points: [number, number][]
}

/** Where a click landed, in world units. Exact, and the browser's own arithmetic. */
export function worldPointOf(
  event: React.MouseEvent<SVGSVGElement>, group: SVGGElement | null,
): [number, number] | null {
  const svg = event.currentTarget
  const matrix = (group ?? svg).getScreenCTM()
  if (!matrix) return null
  const point = svg.createSVGPoint()
  point.x = event.clientX
  point.y = event.clientY
  const world = point.matrixTransform(matrix.inverse())
  return [Math.round(world.x * 10) / 10, Math.round(world.y * 10) / 10]
}

/** Enough points to be the shape it claims to be. */
export function isFinishable(drawing: Drawing): boolean {
  const need = { point: 1, line: 2, polygon: 3 }[drawing.what.kind]
  return drawing.points.length >= need
}

/** The coordinates the API takes, in the nesting each kind wants. */
export function coordinatesOf(drawing: Drawing) {
  if (drawing.what.kind === 'point') return drawing.points[0]
  if (drawing.what.kind === 'line') return drawing.points
  return [drawing.points]
}
