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

import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { ApplyReport, MapDecision, MapFeature, MapPlan } from '../api'
import { ProposalOverlay, ProposalPanel } from './map/ProposalPanel'
import {
  ErrorBox, Loading, Panel, categoryColour, usePanZoom, useAsync,
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
  const [plan, setPlan] = useState<MapPlan | null>(null)
  const [accepted, setAccepted] = useState<Record<string, boolean>>({})
  const [report, setReport] = useState<ApplyReport | null>(null)
  const [genError, setGenError] = useState<string | null>(null)
  const [propose, setPropose] = useState(true)
  // Where the lit ground goes, and whether there is any. The picture itself is fetched
  // by the browser from the src below rather than through the client's data layer: it is
  // an image, it is cached on the server, and asking for it as bytes we then have to
  // turn into an object URL would be three extra steps to arrive at what an <image> tag
  // does on its own.
  const [relief, setRelief] = useState<ReliefBounds | null>(null)
  const [showRelief, setShowRelief] = useState(true)

  useEffect(() => {
    let live = true
    fetch('/api/map/relief')
      .then((response) => (response.ok ? response.json() : { available: false }))
      .then((bounds: ReliefBounds) => {
        if (live) setRelief(bounds.available ? bounds : null)
      })
      .catch(() => {
        // No ground yet is the ordinary case for a world whose map has never been
        // accepted, not an error worth putting in front of the writer.
        if (live) setRelief(null)
      })
    return () => {
      live = false
    }
  }, [day, report])
  const pan = usePanZoom(1)

  // Propose first (§66). The map is worked out and shown; nothing is written until
  // the writer says which of it they want.
  const grow = async () => {
    if (generating) return
    setGenerating(true)
    setGenError(null)
    setReport(null)
    try {
      const proposal = await api.planMap({ invent_settlements: propose })
      setPlan(proposal)
      setAccepted(Object.fromEntries(
        proposal.features.map((f) => [f.id, f.default_accept])))
    } catch (err) {
      setGenError(err instanceof Error ? err.message : String(err))
    } finally {
      setGenerating(false)
    }
  }

  const keep = async (decisions: MapDecision[]) => {
    if (!plan || generating) return
    setGenerating(true)
    setGenError(null)
    try {
      setReport(await api.applyMap(plan, decisions))
      setPlan(null)
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
  const frame = boundsOf(
    visible,
    relief && showRelief
      ? [relief.x, relief.y, relief.x + relief.width, relief.y + relief.height]
      : null,
  )
  const viewBox = `${frame.x} ${frame.y} ${frame.width} ${frame.height}`
  const holderOf = (f: MapFeature) => f.control[mode]?.[0] ?? null
  // A map on which a hamlet, a city and a castle are the same dot is a map that has
  // thrown away most of what the generator worked out. The rank rides on the shape's
  // own style, so the client does not have to know why a place is the size it is.
  const RANK_SIZE: Record<string, number> = {
    city: 8, town: 6.5, village: 5, hamlet: 4,
    castle: 7, keep: 5.5, tower: 4.5,
  }
  const sizeOf = (f: MapFeature, selected: boolean) =>
    (RANK_SIZE[(f.style.rank as string) ?? ''] ?? 6) + (selected ? 2 : 0)
  const fillFor = (f: MapFeature) => {
    const holder = holderOf(f)
    if (holder) return holderColours.get(holder.id) ?? '#7c8590'
    return (f.style.fill as string) ?? '#8a8a8a'
  }
  // With the ground shown, the flat fills are painted over a lit surface that already
  // says everything they were standing in for. The land outline in particular *is* the
  // relief's own coastline, drawn a second time as a sheet of one colour, so it goes
  // entirely; regions keep a wash, because a border still has to be legible.
  const groundShown = Boolean(relief && showRelief)
  const fillOpacityFor = (f: MapFeature, selected: boolean) => {
    if (!groundShown) return selected ? 0.55 : 0.3
    if (f.layer === 'land') return 0
    return selected ? 0.3 : 0.12
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
        {/* Open water past the edge of the rendered ground, so the map reads as a piece
            of a sea rather than as a photograph of a square. As the element's own
            background rather than as a shape inside it: a rect big enough to cover the
            pan range is a couple of thousand pixels across, and Chromium rasterises one
            that size into a tile that lands *over* the relief however early it appears
            in the document. A background cannot be painted over anything. */}
        <svg className="map-svg" viewBox={viewBox} preserveAspectRatio="xMidYMid meet"
             style={groundShown ? { background: OPEN_WATER } : undefined}
             {...pan.handlers} role="img"
             aria-label={`Map of the world on ${day}, coloured by ${mode.replace(/_/g, ' ')}`}>
          <g transform={pan.transform}>
            {/* The ground first of all. Everything else is drawn over it, which is the
                whole point: a coastline that is a stroke on a flat fill reads as a
                diagram, and the same stroke over lit relief reads as a coast. */}
            {relief && showRelief && (
              <image
                href={`/api/map/relief.png?scale=8&v=${encodeURIComponent(relief.updated_at)}`}
                x={relief.x} y={relief.y} width={relief.width} height={relief.height}
                preserveAspectRatio="none"
                style={{ imageRendering: 'auto' }}
              />
            )}
            {/* polygons first, then lines, then points: painter's order */}
            {visible.filter((f) => f.kind === 'polygon').map((f) => (
              <g key={f.id}>
                <path
                  d={polygonPath(f.coordinates as number[][][])}
                  fill={fillFor(f)}
                  fillOpacity={fillOpacityFor(f, selectedId === f.entity_id)}
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
                strokeWidth={
                  // A river and a road both carry their own width: the generator works
                  // one out from how much water passes and the other from how much
                  // traffic does, and a river drawn one width for its whole length — or
                  // a lane drawn as wide as the highway it joins — is one of the
                  // plainest ways a made map differs from a real one.
                  (f.style['stroke-width'] as number | undefined)
                  ?? (f.layer === 'waterways' ? 3.5 : 2.5)
                }
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
              const r = sizeOf(f, selected)
              return (
                <g key={f.id} style={{ cursor: 'pointer' }} onClick={() => onSelect(f.entity_id)}>
                  {/* A castle is not a small town, and drawing it as one loses the whole
                      point of putting it at a pass. A square on its corner reads as a
                      keep at any size, and costs one element. */}
                  {f.layer === 'castles' ? (
                    <rect
                      x={x - r * 0.78} y={y - r * 0.78}
                      width={r * 1.56} height={r * 1.56}
                      transform={`rotate(45 ${x} ${y})`}
                      fill={holder ? holderColours.get(holder.id) : '#8a6113'}
                      stroke="var(--panel)" strokeWidth={2}
                    />
                  ) : (
                    <circle
                      cx={x} cy={y} r={r}
                      fill={holder ? holderColours.get(holder.id) : '#555'}
                      stroke="var(--panel)" strokeWidth={2}
                    />
                  )}
                  {/* A contested place gets a ring as well as a colour — §69. */}
                  {(f.control.claims?.length ?? 0) > 0 && (
                    <circle cx={x} cy={y} r={r + 5} fill="none" stroke="var(--error)"
                            strokeWidth={1.5} strokeDasharray="3 3" />
                  )}
                  {showLabels && (
                    <text className="map-label" x={x + r + 5} y={y + 4}>{f.name}</text>
                  )}
                  <title>{describeControl(f)}</title>
                </g>
              )
            })}
            {plan && <ProposalOverlay plan={plan} accepted={accepted} />}
          </g>
        </svg>

        <div className="map-controls">
          <strong className="small">Layers</strong>
          {relief && (
            <label>
              <input
                type="checkbox"
                checked={showRelief}
                onChange={() => setShowRelief((on) => !on)}
              />{' '}
              the ground
            </label>
          )}
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
                title="Work out land, rivers, cities and roads from your regions —
                       you see it before any of it is written">
          {generating && !plan ? 'Working it out…' : '✦ Propose a map'}
        </button>
        <label className="small" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={propose}
                 onChange={(e) => setPropose(e.target.checked)} />
          suggest settlements I have not named
        </label>
        <span className="spacer" style={{ flex: 1 }} />
        <span className="small muted">
          You see the map before it exists. Nothing you drew is overwritten, and one
          Ctrl+Z undoes whatever you keep.
        </span>
      </div>

      {genError && <div className="error-box small">{genError}</div>}

      {plan && (
        <Panel title="A map, before it exists">
          <ProposalPanel plan={plan} busy={generating}
                         accepted={accepted} setAccepted={setAccepted}
                         onApply={(decisions) => void keep(decisions)}
                         onDiscard={() => setPlan(null)} />
        </Panel>
      )}

      {report && (
        <Panel title="What the map did">
          <p className="small">{report.summary}</p>
          {report.outcomes.filter((o) => o.op === 'promoted').map((o) => (
            <p key={o.feature_id} className="muted small">{o.why}</p>
          ))}
          <ul className="clean small">
            {report.outcomes.filter((o) => o.op === 'created').slice(0, 40).map((o) => (
              <li key={o.feature_id}>
                <strong>{o.name}</strong>
                <div className="muted">{o.why}</div>
              </li>
            ))}
          </ul>
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

interface ReliefBounds {
  available: boolean
  x: number
  y: number
  width: number
  height: number
  updated_at: string
}

/** An SVG viewBox that contains every visible feature, with room for labels. */
/** The deep end of the relief renderer's own sea ramp, so the two meet without a seam. */
const OPEN_WATER = '#4a6580'

interface Frame {
  x: number
  y: number
  width: number
  height: number
}

function boundsOf(features: MapFeature[], ground: number[] | null = null): Frame {
  const xs: number[] = []
  const ys: number[] = []
  if (ground) {
    xs.push(ground[0], ground[2])
    ys.push(ground[1], ground[3])
  }
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
  if (!xs.length) return { x: 0, y: 0, width: 900, height: 800 }

  const margin = 70
  const x = Math.min(...xs) - margin
  const y = Math.min(...ys) - margin
  return {
    x,
    y,
    width: Math.max(...xs) - x + margin,
    height: Math.max(...ys) - y + margin,
  }
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
