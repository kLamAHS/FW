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
import type {
  ApplyReport, MapDecision, MapFeature, MapIcon, MapLabel, MapPlan,
} from '../api'
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
  const [mode, setMode] = useState<ControlMode>('legally_owns')
  const { data, error, loading } = useAsync(
    () => api.map(day, undefined, mode), [day, version, mode])
  const [hidden, setHidden] = useState<Set<string>>(new Set())
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
      const proposal = await api.planMap({
        invent_settlements: propose, at: day,
      })
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
  // layer and at every date. The server assigns the *slot* — by name, so it does not
  // move when a row is added — and the stylesheet says what each slot looks like.
  const holderColours = useMemo(() => {
    const map = new Map<string, string>()
    for (const [id, role] of Object.entries(data?.draw?.holders ?? {})) {
      map.set(id, paint(role))
    }
    return map
  }, [data])

  if (loading && !data) return <Loading what="Drawing the map" />
  if (error) return <ErrorBox error={error} />
  if (!data) return null

  const visible = data.features.filter((f) => !hidden.has(f.layer))
  const draw = data.draw
  const byId = new Map(data.features.map((f) => [f.id, f]))
  // A fictional world's coordinates are whatever the writer drew them as, so the frame is
  // derived from the content rather than assumed. Worked out on the server: the client
  // used to spread every coordinate into `Math.min(...xs)`, which past about sixty-five
  // thousand arguments throws a RangeError and blanks the map with nothing in the
  // console to say why — and a continent with rivers and roads reaches that.
  const frame = draw.bounds
  const viewBox = `${frame.x} ${frame.y} ${frame.width} ${frame.height}`
  const holderOf = (f: MapFeature) => f.control[mode]?.[0] ?? null
  const fillFor = (f: MapFeature) => {
    const holder = holderOf(f)
    if (holder) return holderColours.get(holder.id) ?? 'var(--ink-faint)'
    // A role, resolved through the stylesheet, so the map follows the theme. Older
    // shapes carry a literal colour instead; those are honoured as they are.
    const role = f.style.role as string | undefined
    if (role) return paint(role)
    return (f.style.fill as string) ?? (f.style.stroke as string) ?? 'var(--ink-faint)'
  }
  // With the ground shown, the flat fills are painted over a lit surface that already
  // says everything they were standing in for. The land outline in particular *is* the
  // relief's own coastline, drawn a second time as a sheet of one colour; the woods and
  // marshes are drawn into the relief too, from the same vegetation field. Both go
  // entirely — and they have to, because a wash is not free: eighteen cover polygons at
  // an eighth opacity each compound to nine tenths, and the map was quietly burying a
  // continent's worth of lit relief under its own haze. Regions keep a wash, because
  // whose country this is has to stay legible.
  const groundShown = Boolean(relief && showRelief)
  const REDUNDANT_OVER_GROUND = new Set(['land', 'features', 'waters'])
  const fillOpacityFor = (f: MapFeature, selected: boolean) => {
    if (!groundShown) return selected ? 0.55 : 0.3
    if (REDUNDANT_OVER_GROUND.has(f.layer)) return selected ? 0.25 : 0
    return selected ? 0.3 : 0.12
  }


  return (
    <>
      {/* Open water past the edge of the rendered ground, so the map reads as a piece
          of a sea rather than as a photograph of a square. On the *wrapper*, not on the
          `<svg>`: an SVG root's own background is painted as part of its box and lands
          over its content, which quietly hid the lit ground entirely — the map drew a
          continent's worth of relief every time and then covered it with one flat
          colour. It is not a shape inside the SVG either, because a rect big enough to
          cover the pan range is a couple of thousand pixels across and Chromium
          rasterises one that size into a tile that also lands over the relief. */}
      <div className="map-wrap"
           style={groundShown ? { background: OPEN_WATER } : undefined}>
        <svg className="map-svg" viewBox={viewBox} preserveAspectRatio="xMidYMid meet"
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
              </g>
            ))}

            {visible.filter((f) => f.kind === 'line').map((f) => (
              <path
                key={f.id}
                d={linePath(f.coordinates as number[][])}
                fill="none"
                stroke={fillFor(f)}
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

            {/* Places, each drawn as what it is. A hamlet, a city and a castle were
                all the same dot until now: the generator has known the difference for
                three phases and the picture was throwing it away. */}
            {draw.icons.filter((icon) => !hiddenIcon(icon, hidden, byId)).map((icon) => (
              <Place
                key={icon.key}
                icon={icon}
                fill={icon.holder_role ? paint(icon.holder_role)
                                       : paint(icon.role)}
                selected={selectedId === icon.entity_id}
                onSelect={() => onSelect(icon.entity_id)}
                title={describeControl(byId.get(icon.key))}
              />
            ))}

            {/* Names, placed by the server: on the spine of a country, along a river
                the right way up, and never on top of one another. */}
            {showLabels && draw.labels.map((label) => (
              <Name key={label.key} label={label} />
            ))}
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

      {/* The key. Built from what is actually on this map rather than from a fixed
          list, so a world with no castles is never told to look for one. */}
      {draw.legend.length > 0 && (
        <Panel title="What is on the map">
          <div className="map-legend">
            {draw.legend.map((item) => (
              <button
                key={item.key}
                className={`map-legend-line${item.entity_id ? ' clickable' : ''}`}
                onClick={() => item.entity_id && onSelect(item.entity_id)}
                title={item.note || undefined}
              >
                <Swatch swatch={item.swatch} role={item.role} />
                <span className="name">{item.label}</span>
                {item.note && <span className="note">{item.note}</span>}
              </button>
            ))}
          </div>
          <p className="muted small" style={{ marginTop: 8 }}>
            Legal ownership, administration, occupation, taxation and claims are five
            different facts and are tracked separately — switch above to see each one.
            {draw.unlabelled.length > 0 && ` ${draw.unlabelled.length} name${
              draw.unlabelled.length === 1 ? '' : 's'} would not fit; zoom in for them.`}
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

/** The deep end of the relief renderer's own sea ramp, so the two meet without a seam. */
const OPEN_WATER = 'var(--map-sea)'

/**
 * A colour role, resolved through the stylesheet.
 *
 * Nothing in the generator emits a hex any more (C11): it emits `terrain-mountain` and
 * the stylesheet says what that is in light and in dark. One name, two values, and a
 * writer who finds the marshes muddy can change them in one place.
 */
function paint(role: string): string {
  return `var(--map-${role})`
}

/** Whether this place's layer is switched off. */
function hiddenIcon(icon: MapIcon, hidden: Set<string>,
                    byId: Map<string, MapFeature>): boolean {
  const feature = byId.get(icon.key)
  return feature ? hidden.has(feature.layer) : false
}

/**
 * One place, drawn as what it is.
 *
 * A capital is a star, a city a ring, a town a disc, a hamlet a dot, a castle a square
 * on its corner and a tower a narrow one. §69: the rank is carried by the shape as well
 * as by the size, so it survives being printed in grey.
 */
function Place({ icon, fill, selected, onSelect, title }: {
  icon: MapIcon
  fill: string
  selected: boolean
  onSelect: () => void
  title: string
}) {
  const r = icon.radius + (selected ? 2 : 0)
  const { x, y } = icon
  return (
    <g style={{ cursor: 'pointer' }} onClick={onSelect}>
      {icon.shape === 'star' ? (
        <path d={starPath(x, y, r)} fill={fill} stroke="var(--panel)" strokeWidth={2} />
      ) : icon.shape === 'keep' ? (
        <rect x={x - r * 0.78} y={y - r * 0.78} width={r * 1.56} height={r * 1.56}
              transform={`rotate(45 ${x} ${y})`}
              fill={fill} stroke="var(--panel)" strokeWidth={2} />
      ) : icon.shape === 'tower' ? (
        <rect x={x - r * 0.5} y={y - r} width={r} height={r * 2}
              fill={fill} stroke="var(--panel)" strokeWidth={1.5} />
      ) : icon.shape === 'ring' ? (
        <>
          <circle cx={x} cy={y} r={r} fill="var(--panel)" stroke={fill} strokeWidth={3} />
          <circle cx={x} cy={y} r={r * 0.34} fill={fill} />
        </>
      ) : icon.shape === 'anchor' ? (
        <>
          <circle cx={x} cy={y} r={r} fill={fill} stroke="var(--panel)" strokeWidth={2} />
          <path d={`M${x},${y - r * 0.7}V${y + r * 0.7}M${x - r * 0.6},${y + r * 0.15}
                    A${r * 0.6},${r * 0.6} 0 0 0 ${x + r * 0.6},${y + r * 0.15}`}
                fill="none" stroke="var(--panel)" strokeWidth={1.4} />
        </>
      ) : (
        <circle cx={x} cy={y} r={icon.shape === 'dot' ? r * 0.7 : r}
                fill={fill} stroke="var(--panel)" strokeWidth={2} />
      )}
      {/* A contested place gets a ring as well as a colour — §69. */}
      {icon.contested && (
        <circle cx={x} cy={y} r={r + 5} fill="none" stroke="var(--map-contested)"
                strokeWidth={1.5} strokeDasharray="3 3" />
      )}
      <title>{title}</title>
    </g>
  )
}

/** A five-pointed star, for a capital. Written out rather than computed: the same five
 *  points every time, and no trigonometry in a render path. */
function starPath(x: number, y: number, r: number): string {
  const points = [
    [0, -1], [0.225, -0.309], [0.951, -0.309], [0.363, 0.118], [0.588, 0.809],
    [0, 0.382], [-0.588, 0.809], [-0.363, 0.118], [-0.951, -0.309], [-0.225, -0.309],
  ]
  return points
    .map(([dx, dy], i) => `${i === 0 ? 'M' : 'L'}${x + dx * r},${y + dy * r}`)
    .join(' ') + ' Z'
}

/**
 * One name, where the server put it.
 *
 * Straight names are plain text; a name that really bends runs along a path, which is
 * what lets a country's name follow the country and a river's follow the river. The
 * server never emits an angle — a `textPath` needs none, and computing one would put
 * trigonometry in the deterministic half of the generator.
 */
function Name({ label }: { label: MapLabel }) {
  const className = `map-label${label.role.startsWith('label-')
    ? ` map-${label.role}` : ''}`
  if (label.path && label.path.length > 1) {
    const id = `lp-${label.key}`
    return (
      <>
        <defs>
          <path id={id} d={linePath(label.path)} />
        </defs>
        <text className={className} fontSize={label.size} textAnchor="middle"
              dy={label.size * 0.34}>
          <textPath href={`#${id}`} startOffset="50%">{label.text}</textPath>
        </text>
      </>
    )
  }
  return (
    <text className={className} x={label.x} y={label.y} fontSize={label.size}
          textAnchor={label.anchor}>
      {label.text}
    </text>
  )
}

/** The key's little picture of a thing, so a line in it is recognisable on the map. */
function Swatch({ swatch, role }: { swatch: string; role: string }) {
  const colour = paint(role)
  const box = { width: 18, height: 14 }
  return (
    <svg className="map-legend-swatch" {...box} viewBox="0 0 18 14" aria-hidden="true">
      {swatch === 'fill' ? (
        <rect x={1} y={2} width={16} height={10} rx={2} fill={colour} />
      ) : swatch === 'line' ? (
        <path d="M1,7 C6,2 12,12 17,7" fill="none" stroke={colour} strokeWidth={2.4} />
      ) : swatch === 'dashed' ? (
        <path d="M1,7 H17" fill="none" stroke={colour} strokeWidth={2}
              strokeDasharray="4 3" />
      ) : swatch === 'star' ? (
        <path d={starPath(9, 7, 6)} fill={colour} />
      ) : swatch === 'keep' ? (
        <rect x={5} y={3} width={8} height={8} transform="rotate(45 9 7)" fill={colour} />
      ) : swatch === 'tower' ? (
        <rect x={7} y={2} width={4} height={10} fill={colour} />
      ) : swatch === 'ring' ? (
        <circle cx={9} cy={7} r={4.5} fill="none" stroke={colour} strokeWidth={2.4} />
      ) : swatch === 'anchor' ? (
        <circle cx={9} cy={7} r={4.5} fill={colour} />
      ) : (
        <circle cx={9} cy={7} r={swatch === 'dot' ? 2.6 : 4} fill={colour} />
      )}
    </svg>
  )
}

function describeControl(f: MapFeature | undefined): string {
  if (!f) return ''
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

export { categoryColour }
