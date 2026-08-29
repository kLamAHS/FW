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
