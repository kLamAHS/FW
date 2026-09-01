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

import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type {
  ApplyReport, Entity, MapDecision, MapFeature, MapIcon, MapLabel, MapPlan,
} from '../api'
import { ProposalOverlay, ProposalPanel } from './map/ProposalPanel'
import {
  DRAWABLE, coordinatesOf, isFinishable, worldPointOf,
} from './map/drawing'
import type { Drawable, Drawing } from './map/drawing'
import { EntityPicker } from '../components/forms'
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
  const [mode, setMode] = useState<ControlMode>('legally_owns')
  // §94: whose eyes. Empty is the map from nowhere, which is what the writer sees until
  // they ask for somebody's — a perspective is a lens over the world, never the world.
  const [seenAs, setSeenAs] = useState('')
  // The solver's own working — label boxes, drop reasons — drawn over the map, for
  // tuning the composition rather than for writing (V2 §50).
  const [debug, setDebug] = useState(false)
  const { data, error, loading } = useAsync(
    () => api.map(day, undefined, mode, seenAs || null, debug),
    [day, version, mode, seenAs, debug])
  const eyes = useAsync(() => api.perspectives(), [version])
  const theirView = useAsync(
    () => (seenAs ? api.perspective(seenAs, day) : Promise.resolve(null)),
    [seenAs, day, version])
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
  // Drawing on the map yourself (§66). `null` is the ordinary state — the tool is a
  // mode a writer enters deliberately, because a map that places a town wherever you
  // happened to click is a map you cannot pan.
  const [drawing, setDrawing] = useState<Drawing | null>(null)
  const [choosing, setChoosing] = useState<Drawable | null>(null)
  const [chosen, setChosen] = useState<Entity | null>(null)
  const [drawError, setDrawError] = useState<string | null>(null)
  const [sketch, setSketch] = useState(true)
  // The transformed group, so a click can be turned into world units by the browser's
  // own matrix rather than by arithmetic of ours that would drift from it.
  const surface = useRef<SVGGElement | null>(null)

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
    // The ground is per-branch, not per-day — it changes when a map is accepted
    // (report here, version when the accept happened anywhere else), never with the
    // timeline slider.
  }, [version, report])
  const pan = usePanZoom(1)
  // Which of the three server-solved compositions the zoom is in (V2 §18). The
  // thresholds are the art direction's: world under 1.8, regional to 3.5, local
  // past that. Band picking is purely client-side — no refetch on zoom.
  const band = pan.view.k < 1.8 ? 'world' : pan.view.k <= 3.5 ? 'regional' : 'local'
  const depth = BAND_ORDER[band]

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

  // Escape abandons a drawing, which is the key everybody reaches for. Bound while the
  // tool is open only, so it never competes with anything else on the page.
  useEffect(() => {
    if (!drawing) return undefined
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDrawing(null)
      if (e.key === 'Enter' && isFinishable(drawing)) void save()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const place = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!drawing) return
    const where = worldPointOf(e, surface.current)
    if (!where) return
    setDrawing({ ...drawing, points: [...drawing.points, where] })
  }

  const save = async () => {
    if (!drawing || !isFinishable(drawing)) return
    setDrawError(null)
    try {
      await api.draw({
        entity_id: drawing.entity.id,
        kind: drawing.what.kind,
        layer: drawing.what.layer,
        coordinates: coordinatesOf(drawing),
        style: { role: drawing.what.role },
        // §92. The whole dashed-edge machinery — column, wire shape, stroke, legend
        // line — was plumbed through to pixels for the generator's guesses, and the
        // writer could not say the same thing about their own. A border traced with
        // a mouse at whatever zoom happened to be open is a sketch, so it says so
        // until they tick it surveyed.
        approximate: sketch,
      })
      setDrawing(null)
      onMutate()
    } catch (err) {
      setDrawError(err instanceof Error ? err.message : String(err))
    }
  }

  const erase = async (geometryId: string) => {
    setDrawError(null)
    try {
      await api.erase(geometryId)
      onMutate()
    } catch (err) {
      setDrawError(err instanceof Error ? err.message : String(err))
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
  // The writer's own shapes. `generated` comes from the server's own ledger rule, so
  // this is the same answer the regeneration uses when deciding what is its to replace.
  const mine = data.features.filter((f) => !f.generated)
  // A fictional world's coordinates are whatever the writer drew them as, so the frame is
  // derived from the content rather than assumed. Worked out on the server: the client
  // used to spread every coordinate into `Math.min(...xs)`, which past about sixty-five
  // thousand arguments throws a RangeError and blanks the map with nothing in the
  // console to say why — and a continent with rivers and roads reaches that.
  const frame = draw.bounds
  const viewBox = `${frame.x} ${frame.y} ${frame.width} ${frame.height}`
  const holderOf = (f: MapFeature) => f.control[mode]?.[0] ?? null
  // What a selection's kin are, from what the client already has (V2 §34): the thing
  // itself, and — when a house is selected — everything that house holds under the
  // authority the map is currently coloured by. No request, no re-solve: the names
  // are already placed, and quieting the rest is a matter of opacity.
  const kin = useMemo(() => {
    if (!selectedId) return null
    const own = new Set<string>([selectedId])
    for (const f of data.features) {
      if (f.entity_id === selectedId) own.add(f.entity_id)
      for (const holder of f.control[mode] ?? []) {
        if (holder.id === selectedId) own.add(f.entity_id)
      }
    }
    return own
  }, [selectedId, data.features, mode])
  const isKin = (entityId: string) => !kin || kin.has(entityId)
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
             // While the tool is open a click places a corner rather than starting a
             // drag: a surface that pans under the cursor cannot be drawn on. The wheel
             // still zooms, because drawing at one scale is how a border goes wrong.
             {...(drawing ? { onWheel: pan.handlers.onWheel } : pan.handlers)}
             onClick={drawing ? place : undefined}
             style={drawing ? { cursor: 'crosshair' } : undefined}
             role="img"
             aria-label={`Map of the world on ${day}, coloured by ${mode.replace(/_/g, ' ')}`}>
          {/* §69: colour must never be the only thing carrying a distinction. A
              contested *point* has had a ring since the icons were written; a contested
              region had nothing but its fill, so "whose country is this" was hue plus a
              legend, and the hover title is not available to keyboard or touch. */}
          <defs>
            <pattern id="contested-hatch" width="8" height="8"
                     patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="8"
                    stroke="var(--map-contested)" strokeWidth="1.6" />
            </pattern>
          </defs>
          <g ref={surface} transform={pan.transform}
             className={kin ? 'map-focus--on' : undefined}>
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
              <g key={f.id}
                 className={`map-quiet${isKin(f.entity_id) ? ' map-kin' : ''}`}>
                <path
                  d={polygonPath(f.coordinates as number[][][])}
                  fill={fillFor(f)}
                  fillOpacity={fillOpacityFor(f, selectedId === f.entity_id)}
                  // A region polygon marked edge:none carries no stroke of its own:
                  // its frontiers arrive as shared border arcs, each stroked once,
                  // and its seaward side is the coastline itself. Stroking the ring
                  // anyway drew every internal border twice and the coast three
                  // times. Selection still gets its ring — emphasis, not geography.
                  stroke={f.style.edge === 'none' && selectedId !== f.entity_id
                    ? 'none' : fillFor(f)}
                  strokeWidth={selectedId === f.entity_id ? 3
                    : f.layer === 'waters' ? 1 : 1.5}
                  // Water is not a proposal. The dashed edge says "the map guessed
                  // this line and you may move it", which is true of a border and
                  // false of a lake: the shore of standing water is where the ground
                  // stops, and drawing it dashed over the rendered mere below it
                  // made one lake look like two disagreeing ones.
                  strokeDasharray={f.approximate && f.layer !== 'waters'
                    ? '7 5' : undefined}
                  style={{ cursor: 'pointer' }}
                  onClick={() => onSelect(f.entity_id)}
                >
                  <title>{describeControl(f)}</title>
                </path>
                {isContested(f) && (
                  <path
                    d={polygonPath(f.coordinates as number[][][])}
                    fill="url(#contested-hatch)"
                    stroke="none"
                    pointerEvents="none"
                  >
                    <title>{describeControl(f)}</title>
                  </path>
                )}
              </g>
            ))}

            {visible.filter((f) => f.kind === 'line').map((f) => (
              <path
                key={f.id}
                className={`map-quiet${isKin(f.entity_id) ? ' map-kin' : ''}`}
                d={linePath(f.coordinates as number[][])}
                fill="none"
                // A border arc is drawn in border ink whoever holds the ground: in
                // political mode the *fills* say who holds what, and a frontier that
                // switched to its owner's colour would read as belonging to one side.
                // A shore run is coastline ink for the same reason — it is the edge
                // of the land itself, not of whoever currently holds it.
                stroke={(f.style.role as string) === 'border' ? paint('border')
                  : (f.style.role as string) === 'coastline' ? paint('coastline')
                  : fillFor(f)}
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
            {/* Icons arrive with the widest band each belongs to; groups fade over
                150ms as the zoom crosses a threshold, so leaning in reveals the
                villages rather than popping them. A hidden group must not catch
                clicks meant for the map under it. */}
            {BANDS.map((b) => (
              <g key={`icons-${b}`} className="map-band"
                 style={{ opacity: BAND_ORDER[b] <= depth ? 1 : 0 }}
                 pointerEvents={BAND_ORDER[b] <= depth ? undefined : 'none'}>
                {draw.icons
                  .filter((icon) => (icon.band ?? 'world') === b)
                  .filter((icon) => !hiddenIcon(icon, hidden, byId)).map((icon) => (
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
              </g>
            ))}

            {/* Names, placed by the server: on the spine of a country, along a river
                the right way up, and never on top of one another — solved once per
                zoom band, and only the band the zoom is in shows (V2 §18). */}
            {showLabels && BANDS.map((b) => (
              <g key={`names-${b}`} className="map-band"
                 style={{ opacity: b === band ? 1 : 0 }} pointerEvents="none">
                {(draw.labels[b] ?? []).map((label) => (
                  <Name key={label.key} label={label} />
                ))}
              </g>
            ))}
            {debug && (draw.labels[band] ?? []).map((label) =>
              (label.boxes ?? []).map((b, i) => (
              <rect key={`${label.key}-box-${i}`} x={b[0]} y={b[1]}
                    width={b[2] - b[0]} height={b[3] - b[1]} fill="none"
                    stroke="var(--map-contested)" strokeWidth={0.8}
                    strokeDasharray="2 2" pointerEvents="none" />
            )))}
            {plan && <ProposalOverlay plan={plan} accepted={accepted} />}
            {drawing && <InProgress drawing={drawing} />}
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
          <label title="The solver's working: label boxes on the map, and why each
missing name was dropped">
            <input type="checkbox" checked={debug}
                   onChange={() => setDebug((v) => !v)} />
            label debug
          </label>
          <button onClick={pan.reset} style={{ marginTop: 4 }}>Fit the map</button>
          <button disabled={!selectedId} title="Zoom to what is selected"
                  onClick={() => {
                    // Fit the selected thing with breathing room (V2 §35). The
                    // transform maps a content point p to x + k·p in viewBox
                    // units, so centring is one line of algebra per axis.
                    if (!selectedId) return
                    const xs: number[] = []
                    const ys: number[] = []
                    const gather = (node: unknown) => {
                      if (!Array.isArray(node)) return
                      if (node.length === 2 && typeof node[0] === 'number'
                          && typeof node[1] === 'number') {
                        xs.push(node[0])
                        ys.push(node[1])
                        return
                      }
                      for (const child of node) gather(child)
                    }
                    data.features.filter((f) => f.entity_id === selectedId)
                      .forEach((f) => gather(f.coordinates))
                    if (!xs.length) return
                    const pad = 60
                    const x0 = Math.min(...xs) - pad
                    const x1 = Math.max(...xs) + pad
                    const y0 = Math.min(...ys) - pad
                    const y1 = Math.max(...ys) + pad
                    const k = Math.min(6, Math.max(0.08, Math.min(
                      frame.width / (x1 - x0), frame.height / (y1 - y0))))
                    pan.setView({
                      k,
                      x: frame.x + frame.width / 2 - (k * (x0 + x1)) / 2,
                      y: frame.y + frame.height / 2 - (k * (y0 + y1)) / 2,
                    })
                  }}>
            Centre on selection
          </button>
        </div>
      </div>

      <div className="toolbar" style={{ marginTop: 12 }}>
        <span className="small muted">Colour by</span>
        {CONTROL_MODES.map((m) => (
          <button key={m.key} className={mode === m.key ? 'active' : ''}
                  onClick={() => setMode(m.key)}>
            {m.label}
          </button>
        ))}

        {/* §93, §94. The list is only those who have said something — an account, a
            claim, an ignorance — because a picker of every entity in the world would be
            hundreds of choices that change nothing. */}
        {(eyes.data ?? []).length > 0 && (
          <>
            <span className="spacer" style={{ flex: 1 }} />
            <label className="small muted" htmlFor="map-perspective">seen by</label>
            <select id="map-perspective" value={seenAs}
                    onChange={(e) => setSeenAs(e.target.value)}>
              <option value="">nobody in particular</option>
              {(eyes.data ?? []).map((who) => (
                <option key={who.id} value={who.id}>{who.name}</option>
              ))}
            </select>
          </>
        )}
      </div>

      {/* §67: a view that quietly altered the map would be the purest black box, so it
          says whose it is and exactly what it changes. */}
      {seenAs && (
        <Panel title={`As ${data?.seen_as_name || 'they'} see${
                 data?.seen_as_name ? 's' : ''} it`}>
          <div className="toolbar">
            <span className="small muted">
              Places they have never heard of are missing, they are called what this
              party calls them, and ground they claim is shown as theirs.
            </span>
            <span className="spacer" style={{ flex: 1 }} />
            <button onClick={() => setSeenAs('')}>Back to the world itself</button>
          </div>
          <ul className="clean small" style={{ marginTop: 6 }}>
            {(theirView.data?.differences ?? []).map((d, i) => (
              <li key={i} className="difference">
                <Badge>{d.kind}</Badge> {d.text}
                {d.evidence[0] && (
                  <div className="muted" style={{ marginLeft: 6 }}>{d.evidence[0]}</div>
                )}
              </li>
            ))}
          </ul>
          {(theirView.data?.differences ?? []).length === 0 && (
            <p className="muted small">
              Nothing they have said changes this map yet.
            </p>
          )}
        </Panel>
      )}

      {/* Drawing it yourself (§66). The generator is built entirely around honouring
          what the writer drew — it refuses to redraw a region they outlined and grows
          the coastline to fit their borders — and until now nothing could draw one. */}
      <div className="toolbar" style={{ marginTop: 12 }}>
        <span className="small muted">Draw</span>
        {DRAWABLE.map((what) => (
          <button key={what.key}
                  className={choosing?.key === what.key ? 'active' : ''}
                  onClick={() => {
                    setDrawing(null)
                    setChosen(null)
                    setChoosing(choosing?.key === what.key ? null : what)
                  }}>
            {what.label}
          </button>
        ))}
        {choosing && (
          <>
            <EntityPicker label="" chosen={chosen}
                          onChoose={(entity) => {
                            setChosen(entity)
                            if (entity) {
                              setDrawing({ what: choosing, entity, points: [] })
                              setChoosing(null)
                            }
                          }} />
            <span className="small muted">whose {choosing.label} is it?</span>
          </>
        )}
        {drawing && (
          <>
            <span className="small">
              <strong>{drawing.entity.name}</strong> — {drawing.what.hint}{' '}
              {drawing.points.length} placed.
            </span>
            <label className="small" title="A dashed edge is a shape you mean roughly">
              <input type="checkbox" checked={sketch}
                     onChange={(e) => setSketch(e.target.checked)} />{' '}
              roughly
            </label>
            <button className="active" disabled={!isFinishable(drawing)}
                    onClick={() => void save()}>Done</button>
            <button onClick={() => setDrawing({ ...drawing,
                                                points: drawing.points.slice(0, -1) })}
                    disabled={!drawing.points.length}>Undo a corner</button>
            <button onClick={() => setDrawing(null)}>Cancel</button>
          </>
        )}
      </div>
      {drawError && <div className="error-box small">{drawError}</div>}

      {/* What they have drawn, and the way to rub one out. Only their own shapes: the
          map's are managed by accepting or rejecting a proposal. */}
      {mine.length > 0 && (
        <Panel title="Drawn by you" count={mine.length}>
          {mine.map((f) => (
            <div key={f.id} className="entity-line">
              <button className="name" style={{ border: 0, background: 'none' }}
                      onClick={() => onSelect(f.entity_id)}>{f.name}</button>
              <Badge>{f.kind}</Badge>
              <span className="desc">on {f.layer}</span>
              <button className="danger" onClick={() => void erase(f.id)}>rub out</button>
            </div>
          ))}
        </Panel>
      )}

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
              draw.unlabelled.length === 1 ? '' : 's'} would not fit.`}
          </p>
          {debug && draw.unlabelled.length > 0 && (
            <ul className="clean small muted">
              {draw.unlabelled.map((gone) => (
                <li key={gone.key}>{gone.text} — {gone.reason}</li>
              ))}
            </ul>
          )}
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

/**
 * The shape as it is being drawn.
 *
 * Corners as well as the line between them, because a writer placing the fourth corner
 * of a border needs to see the three they have already placed — a bare polyline shows
 * where the shape is going and not where it has been.
 */
function InProgress({ drawing }: { drawing: Drawing }) {
  const points = drawing.points
  if (!points.length) return null
  const path = points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x},${y}`).join(' ')
  return (
    <g className="map-drawing" pointerEvents="none">
      {points.length > 1 && (
        <path d={drawing.what.kind === 'polygon' ? `${path} Z` : path}
              fill={drawing.what.kind === 'polygon' ? 'var(--map-border)' : 'none'}
              fillOpacity={0.18} stroke="var(--map-border)" strokeWidth={2}
              strokeDasharray="6 4" strokeLinejoin="round" />
      )}
      {points.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={4} fill="var(--map-contested)"
                stroke="var(--panel)" strokeWidth={1.5} />
      ))}
    </g>
  )
}

/** The deep end of the relief renderer's own sea ramp, so the two meet without a seam. */
const OPEN_WATER = 'var(--map-sea)'

/** The three views of one map, widest first — the server solves labels per band. */
const BANDS = ['world', 'regional', 'local'] as const
const BAND_ORDER: Record<string, number> = { world: 0, regional: 1, local: 2 }

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
// The server decides each label's voice — face, weight, style, tracking — because its
// collision boxes are measured from exactly those choices against the bundled fonts.
// The stylesheet keeps only what the solver cannot feel: colour and halo. A face key
// maps here to the family the em tables were measured from.
const FACE_CSS: Record<string, { family: string; weight: number; style: string }> = {
  serif: { family: 'var(--map-serif)', weight: 400, style: 'normal' },
  'serif-italic': { family: 'var(--map-serif)', weight: 400, style: 'italic' },
  sc: { family: 'var(--map-sc)', weight: 400, style: 'normal' },
  sans: { family: 'var(--map-sans)', weight: 400, style: 'normal' },
  'sans-medium': { family: 'var(--map-sans)', weight: 500, style: 'normal' },
  'sans-bold': { family: 'var(--map-sans)', weight: 700, style: 'normal' },
}

function Name({ label }: { label: MapLabel }) {
  const className = `map-label${label.role.startsWith('label-')
    ? ` map-${label.role}` : ''}`
  const face = FACE_CSS[label.face ?? 'serif'] ?? FACE_CSS.serif
  const voice = {
    fontFamily: face.family,
    fontWeight: face.weight,
    fontStyle: face.style,
    letterSpacing: `${label.tracking ?? 0}em`,
    strokeWidth: `${label.halo ?? 3}px`,
  }
  if (label.path && label.path.length > 1) {
    const id = `lp-${label.key}`
    return (
      <>
        <defs>
          <path id={id} d={linePath(label.path)} />
        </defs>
        <text className={className} fontSize={label.size} textAnchor="middle"
              dy={label.size * 0.34} style={voice}>
          <textPath href={`#${id}`} startOffset="50%">{label.text}</textPath>
        </text>
      </>
    )
  }
  return (
    <text className={className} x={label.x} y={label.y} fontSize={label.size}
          textAnchor={label.anchor} style={voice}>
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

/** Somebody claims this ground and somebody else holds it (§11, §69). */
function isContested(feature: MapFeature): boolean {
  return ((feature.control ?? {}).claims ?? []).length > 0
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
