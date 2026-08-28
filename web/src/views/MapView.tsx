/**
 * The map (§34, §35, §36, §11).
 *
 * Three things the brief insists on, all visible here at once:
 *
 * - **Layers** (§35) toggle independently and compose, so "House Veyne's holdings plus
 *   the major roads plus grain production" is one view rather than three.
 * - **Time** (§36) is not a filter bolted on; the map is drawn from the world-state query,
 *   so moving the slider changes borders, ownership, which settlements exist and who holds
 *   them, because those are different facts on different dates rather than edits.
 * - **Control is four-way** (§11). Colouring a region by "its owner" would flatten the
 *   distinction the brief calls critical, so the fill follows whichever authority the
 *   reader has asked to see, and the legend says which one that is.
 *
 * Rendered as plain SVG. A fictional map has no coordinate reference system, so a real
 * mapping library would spend most of its effort on machinery this world does not have.
 */

import { useMemo, useState } from 'react'
import { api } from '../api'
import type { MapFeature, MapGenerationReport } from '../api'
import {
  Badge, ErrorBox, Loading, Panel, categoryColour, usePanZoom, useAsync,
} from '../components/common'

const CONTROL_MODES = [
  { key: 'legally_owns', label: 'Legal owner' },
  { key: 'administers', label: 'Administered by' },
  { key: 'occupies', label: 'Militarily occupied by' },
  { key: 'taxes', label: 'Taxed by' },
  { key: 'claims', label: 'Claimed by' },
] as const

type ControlMode = (typeof CONTROL_MODES)[number]['key']

interface Props {
  day: number
  onSelect: (id: string) => void
  selectedId: string | null
  version: number
  onMutate: () => void
}

export function MapView({ day, onSelect, selectedId, version, onMutate }: Props) {
  const { data, error, loading } = useAsync(() => api.map(day), [day, version])
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [mode, setMode] = useState<ControlMode>('legally_owns')
  const [showLabels, setShowLabels] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [report, setReport] = useState<MapGenerationReport | null>(null)
  const [genError, setGenError] = useState<string | null>(null)
  const [propose, setPropose] = useState(true)
  const pan = usePanZoom(1)

  const grow = async () => {
    if (generating) return
    setGenerating(true)
    setGenError(null)
    try {
      setReport(await api.generateMap(null, propose))
      onMutate()
    } catch (err) {
      setGenError(err instanceof Error ? err.message : String(err))
    } finally {
      setGenerating(false)
    }
  }

  // A stable colour per controlling house, so the same house reads the same on every
  // layer and at every date.
  const holderColours = useMemo(() => {
    const palette = ['#7a5c3e', '#2f5d8a', '#3f6b52', '#a2332c', '#6b3f7a',
                     '#8a6113', '#557a8a', '#a2456a']
    const map = new Map<string, string>()
    for (const feature of data?.features ?? []) {
      for (const holders of Object.values(feature.control)) {
        for (const holder of holders) {
          if (!map.has(holder.id)) map.set(holder.id, palette[map.size % palette.length])
        }
      }
    }
    return map
  }, [data])

  if (loading && !data) return <Loading what="Drawing the map" />
  if (error) return <ErrorBox error={error} />
  if (!data) return null

  const visible = data.features.filter((f) => !hidden.has(f.layer))
  // A fictional world's coordinates are whatever the writer drew them as, so the frame is
  // derived from the content rather than assumed. Without this the map floats small in a
  // mostly empty rectangle, which is precisely the "make the reader imagine it" failure
  // §70 is about.
  const viewBox = boundsOf(visible)
  const holderOf = (f: MapFeature) => f.control[mode]?.[0] ?? null
  const fillFor = (f: MapFeature) => {
    const holder = holderOf(f)
    if (holder) return holderColours.get(holder.id) ?? '#7c8590'
    return (f.style.fill as string) ?? '#8a8a8a'
  }

  const legend = [...holderColours.entries()]
    .map(([id, colour]) => {
      const named = data.features
        .flatMap((f) => f.control[mode] ?? [])
        .find((h) => h.id === id)
      return named ? { id, colour, name: named.name } : null
    })
    .filter((x): x is { id: string; colour: string; name: string } => x !== null)
    .filter((x, i, all) => all.findIndex((y) => y.id === x.id) === i)

  return (
    <>
      <div className="map-wrap">
        <svg className="map-svg" viewBox={viewBox} preserveAspectRatio="xMidYMid meet"
             {...pan.handlers} role="img"
             aria-label={`Map of the world on ${day}, coloured by ${mode.replace(/_/g, ' ')}`}>
          <g transform={pan.transform}>
            {/* polygons first, then lines, then points: painter's order */}
            {visible.filter((f) => f.kind === 'polygon').map((f) => (
              <g key={f.id}>
                <path
                  d={polygonPath(f.coordinates as number[][][])}
                  fill={fillFor(f)}
                  fillOpacity={selectedId === f.entity_id ? 0.55 : 0.3}
                  stroke={fillFor(f)}
                  strokeWidth={selectedId === f.entity_id ? 3 : 1.5}
                  strokeDasharray={f.approximate ? '7 5' : undefined}
                  style={{ cursor: 'pointer' }}
                  onClick={() => onSelect(f.entity_id)}
                >
                  <title>{describeControl(f)}</title>
                </path>
                {showLabels && (
                  <text className="map-region-label" textAnchor="middle"
                        {...centroid(f.coordinates as number[][][])}>
                    {f.name}
                  </text>
                )}
              </g>
            ))}

            {visible.filter((f) => f.kind === 'line').map((f) => (
              <path
                key={f.id}
                d={linePath(f.coordinates as number[][])}
                fill="none"
                stroke={(f.style.stroke as string) ?? '#666'}
                strokeWidth={f.layer === 'waterways' ? 3.5 : 2.5}
                strokeDasharray={f.style.dash ? '6 4' : undefined}
                strokeLinecap="round"
                strokeLinejoin="round"
                style={{ cursor: 'pointer' }}
                onClick={() => onSelect(f.entity_id)}
              >
                <title>{f.name}</title>
              </path>
            ))}

            {visible.filter((f) => f.kind === 'point').map((f) => {
              const [x, y] = f.coordinates as number[]
              const holder = holderOf(f)
              const selected = selectedId === f.entity_id
              return (
                <g key={f.id} style={{ cursor: 'pointer' }} onClick={() => onSelect(f.entity_id)}>
                  <circle
                    cx={x} cy={y} r={selected ? 8 : 6}
                    fill={holder ? holderColours.get(holder.id) : '#555'}
                    stroke="var(--panel)" strokeWidth={2}
                  />
                  {/* A contested place gets a ring as well as a colour — §69. */}
                  {(f.control.claims?.length ?? 0) > 0 && (
                    <circle cx={x} cy={y} r={11} fill="none" stroke="var(--error)"
                            strokeWidth={1.5} strokeDasharray="3 3" />
                  )}
                  {showLabels && (
                    <text className="map-label" x={x + 11} y={y + 4}>{f.name}</text>
                  )}
                  <title>{describeControl(f)}</title>
                </g>
              )
            })}
          </g>
        </svg>

        <div className="map-controls">
          <strong className="small">Layers</strong>
          {data.layers.map((layer) => (
            <label key={layer}>
              <input
                type="checkbox"
                checked={!hidden.has(layer)}
                onChange={() => setHidden((prev) => {
                  const next = new Set(prev)
                  next.has(layer) ? next.delete(layer) : next.add(layer)
                  return next
                })}
              />
              {layer}
            </label>
          ))}
          <label>
            <input type="checkbox" checked={showLabels}
                   onChange={() => setShowLabels((v) => !v)} />
            labels
          </label>
          <button onClick={pan.reset} style={{ marginTop: 4 }}>Reset view</button>
        </div>
      </div>

      {/* §34: grow the map from what the regions already say about themselves. */}
      <div className="toolbar" style={{ marginTop: 12 }}>
        <button className="active" disabled={generating} onClick={() => void grow()}
                title="Draw land, rivers, cities and roads from your regions">
          {generating ? 'Growing the map…' : '✦ Generate the map'}
        </button>
        <label className="small" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={propose}
                 onChange={(e) => setPropose(e.target.checked)} />
          suggest settlements I have not named
        </label>
        <span className="spacer" style={{ flex: 1 }} />
        <span className="small muted">
          Nothing you drew is overwritten, and one Ctrl+Z undoes the whole map.
        </span>
      </div>

      {genError && <div className="error-box small">{genError}</div>}

      {report && (
        <Panel title="What the map did">
          <p className="small">{report.summary}</p>
          {report.notes.map((note, i) => (
            <p key={i} className="muted small">{note}</p>
          ))}
          {report.regions_kept.length > 0 && (
            <p className="muted small">
              Left exactly as you drew them: {report.regions_kept.join(', ')}.
            </p>
          )}
          {report.placements.length > 0 && (
            <ul className="clean small">
              {report.placements.map((p) => (
                <li key={p.name}>
                  <button className="link" disabled={!p.entity_id}
                          onClick={() => p.entity_id && onSelect(p.entity_id)}>
                    {p.name}
                  </button>
                  {p.proposed && <Badge>suggested</Badge>}
                  <div className="muted">{p.why}</div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      <div className="toolbar" style={{ marginTop: 12 }}>
        <span className="small muted">Colour by</span>
        {CONTROL_MODES.map((m) => (
          <button key={m.key} className={mode === m.key ? 'active' : ''}
                  onClick={() => setMode(m.key)}>
            {m.label}
          </button>
        ))}
      </div>

      {legend.length > 0 && (
        <Panel title={CONTROL_MODES.find((m) => m.key === mode)!.label}>
          <div className="stat-row">
            {legend.map((item) => (
              <button key={item.id} className="entity-line" style={{ width: 'auto' }}
                      onClick={() => onSelect(item.id)}>
                <span style={{
                  width: 12, height: 12, borderRadius: 3, background: item.colour,
                  display: 'inline-block',
                }} />
                <span className="name">{item.name}</span>
              </button>
            ))}
          </div>
          <p className="muted small" style={{ marginTop: 8 }}>
            A dashed ring marks a contested place. Legal ownership, administration,
            occupation, taxation and claims are tracked separately — switch above to see
            each one.
          </p>
        </Panel>
      )}
    </>
  )
}

/** An SVG viewBox that contains every visible feature, with room for labels. */
function boundsOf(features: MapFeature[]): string {
  const xs: number[] = []
  const ys: number[] = []
  const walk = (node: unknown): void => {
    if (!Array.isArray(node)) return
    if (node.length === 2 && typeof node[0] === 'number' && typeof node[1] === 'number') {
      xs.push(node[0])
      ys.push(node[1])
      return
    }
    node.forEach(walk)
  }
  features.forEach((f) => walk(f.coordinates))
  if (!xs.length) return '0 0 900 800'

  const margin = 70
  const minX = Math.min(...xs) - margin
  const minY = Math.min(...ys) - margin
  const width = Math.max(...xs) - minX + margin
  const height = Math.max(...ys) - minY + margin
  return `${minX} ${minY} ${width} ${height}`
}

function describeControl(f: MapFeature): string {
  const parts = [f.name]
  for (const [predicate, holders] of Object.entries(f.control)) {
    parts.push(`${predicate.replace(/_/g, ' ')}: ${holders.map((h) => h.name).join(', ')}`)
  }
  return parts.join('\n')
}

function polygonPath(rings: number[][][]): string {
  return rings
    .map((ring) => ring.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x},${y}`).join(' ') + ' Z')
    .join(' ')
}

function linePath(points: number[][]): string {
  return points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x},${y}`).join(' ')
}

function centroid(rings: number[][][]): { x: number; y: number } {
  const points = rings[0] ?? []
  if (!points.length) return { x: 0, y: 0 }
  const sum = points.reduce((acc, [x, y]) => [acc[0] + x, acc[1] + y], [0, 0])
  return { x: sum[0] / points.length, y: sum[1] / points.length }
}

export { categoryColour }
