/**
 * Travel and logistics (§22).
 *
 * "How long does it take to travel from Greyhaven to Rennford?" — asked once, answered for
 * every way of making the journey at once, because the interesting fact is usually the
 * comparison. A courier arriving in two days while the army needs twelve is the shape of a
 * great many plots.
 *
 * "No route" is displayed as an answer rather than an error: a frozen river or an unbuilt
 * road is exactly the kind of constraint a writer wants to discover.
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { RouteResult } from '../api'
import { ErrorBox, Loading, Panel, useAsync } from '../components/common'

interface Props {
  day: number
  dateText: string
}

export function TravelView({ day, dateText }: Props) {
  // Everywhere the routes reach, not every settlement: the map draws a crossing to
  // each island it makes, and an island is a place a ship puts in at rather than a
  // town — so a picker built from settlements could not offer the one journey the
  // crossing exists for.
  const places = useAsync(() => api.travelPlaces(), [day])
  const vocabulary = useAsync(() => api.vocabulary(), [])
  const [origin, setOrigin] = useState('')
  const [destination, setDestination] = useState('')
  const [party, setParty] = useState('')
  const [results, setResults] = useState<
    { profile: string; label: string; route: RouteResult | null; error?: string }[] | null
  >(null)
  const [busy, setBusy] = useState(false)
  // §20/§21: a road the writer knows about and the map never drew. The engine could
  // always route over one; only the generator and the seed could make one.
  const [laying, setLaying] = useState(false)
  const [road, setRoad] = useState({ medium: 'road', terrain: 'plain', length: '40',
                                     quality: '0.8' })
  const [roadError, setRoadError] = useState<string | null>(null)

  // Default to the first two places so the view is useful the moment it opens.
  useEffect(() => {
    if (!origin && places.data && places.data.length >= 2) {
      setOrigin(places.data[0].id)
      setDestination(places.data[1].id)
    }
  }, [places.data, origin])

  const run = async () => {
    if (!origin || !destination || origin === destination) return
    setBusy(true)
    const profiles = vocabulary.data?.transport_profiles ?? []
    const out = await Promise.all(profiles.map(async (p) => {
      try {
        return { profile: p.key, label: p.label, route: await api.route(origin, destination, p.key, day) }
      } catch (err) {
        return {
          profile: p.key, label: p.label, route: null,
          error: err instanceof Error ? err.message : 'no route',
        }
      }
    }))
    setResults(out)
    setBusy(false)
  }

  const lay = async () => {
    setRoadError(null)
    try {
      await api.layRoad({
        from_entity_id: origin, to_entity_id: destination,
        length: Number(road.length), medium: road.medium,
        quality: Number(road.quality), terrain: road.terrain,
      })
      setLaying(false)
      setResults(null)
      await run()
    } catch (err) {
      setRoadError(err instanceof Error ? err.message : String(err))
    }
  }

  if (places.loading) return <Loading />
  if (places.error) return <ErrorBox error={places.error} />

  const nameOf = (id: string) => places.data?.find((p) => p.id === id)?.name ?? ''

  return (
    <>
      <Panel title="How long does the journey take?">
        <div className="toolbar">
          <label>
            <div className="small muted">From</div>
            <select value={origin} onChange={(e) => setOrigin(e.target.value)}>
              {(places.data ?? []).map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
          <label>
            <div className="small muted">To</div>
            <select value={destination} onChange={(e) => setDestination(e.target.value)}>
              {(places.data ?? []).map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
          <label>
            <div className="small muted">Party size</div>
            <input type="number" min={1} placeholder="any" value={party}
                   onChange={(e) => setParty(e.target.value)} style={{ width: 90 }} />
          </label>
          <button onClick={run} disabled={busy || origin === destination}
                  style={{ alignSelf: 'flex-end' }}>
            {busy ? 'Working…' : 'Work it out'}
          </button>
        </div>
        <p className="muted small">
          On {dateText}. Season and construction dates both apply, so the answer changes
          with the timeline above.
        </p>

        {/* §20, §21. "No route" is an answer rather than an error — and a writer who
            knows there *is* a road had no way to say so. */}
        {laying ? (
          <div className="toolbar" style={{ flexWrap: 'wrap' }}>
            <span className="small">
              {nameOf(origin)} → {nameOf(destination)}, by
            </span>
            <select value={road.medium}
                    onChange={(e) => {
                      const wet = (vocabulary.data?.route_sailed ?? [])
                        .includes(e.target.value)
                      setRoad({
                        ...road, medium: e.target.value,
                        // A boat road over dry ground scores zero against every
                        // profile and vanishes from every route, so the ground follows
                        // the way rather than waiting to be got wrong.
                        terrain: wet ? 'water'
                          : road.terrain === 'water' ? 'plain' : road.terrain,
                      })
                    }}>
              {(vocabulary.data?.route_media ?? []).map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <select value={road.terrain}
                    onChange={(e) => setRoad({ ...road, terrain: e.target.value })}>
              {(vocabulary.data?.route_terrains ?? []).map((t) => (
                <option key={t} value={t}>over {t}</option>
              ))}
            </select>
            <label className="small">
              length
              <input type="number" min={1} value={road.length} style={{ width: 80 }}
                     onChange={(e) => setRoad({ ...road, length: e.target.value })} />
            </label>
            <label className="small">
              quality
              <input type="number" min={0.05} max={1} step={0.05} value={road.quality}
                     style={{ width: 80 }}
                     onChange={(e) => setRoad({ ...road, quality: e.target.value })} />
            </label>
            <button className="active" onClick={() => void lay()}
                    disabled={origin === destination}>Lay it</button>
            <button onClick={() => { setLaying(false); setRoadError(null) }}>
              Cancel
            </button>
          </div>
        ) : (
          <button onClick={() => setLaying(true)} disabled={origin === destination}>
            + a road of your own between these two
          </button>
        )}
        {roadError && <div className="error-box small">{roadError}</div>}
      </Panel>

      {results && (
        <Panel title={`${nameOf(origin)} to ${nameOf(destination)}`}>
          <div className="scroll-x">
            <table className="data">
              <thead>
                <tr>
                  <th>By</th><th>Days</th><th>Distance</th><th>Route</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r) => (
                  <tr key={r.profile}>
                    <td><strong>{r.label}</strong></td>
                    <td className="mono">
                      {r.route ? r.route.days.toFixed(1) : <span className="muted">—</span>}
                    </td>
                    <td className="mono">
                      {r.route ? r.route.distance.toFixed(0) : ''}
                    </td>
                    <td className="small">
                      {r.route
                        ? r.route.path_names.join(' → ')
                        : <span className="muted">{r.error}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  )
}
