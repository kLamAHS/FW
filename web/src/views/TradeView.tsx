/**
 * Where a place gets what it does not grow (§19, §41, §42, §86).
 *
 * §19 asks this system one concrete question — *"Where does Greyhaven get its grain?"* —
 * and expects the supply path traced. The facts were all there and nothing joined them:
 * the seed records that Greyhaven imports grain and the Vale exports it, and the router
 * has always been able to cost the journey between two places.
 *
 * Not a simulation, and deliberately so (§68, §116). Nothing here computes a yield from
 * soil and labour; it traces what the writer wrote and asks how long the road takes on
 * the day the timeline is showing. Which is why the *negative* answers are the ones set
 * in bold: a town whose only supplier is unreachable, or which needs something nobody in
 * the world makes, is a story — and that is what a writer opened this screen for.
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Finding, SupplyNeed } from '../api'
import { Badge, ErrorBox, Loading, Panel, useAsync } from '../components/common'

interface Props {
  day: number
  dateText: string
  onSelect: (id: string) => void
  version: number
}

/** The kinds worth shouting about, and how loudly. */
const ALARM: Record<string, string> = {
  gap: 'error',
  fragile: 'disputed',
}

export function TradeView({ day, dateText, onSelect, version }: Props) {
  const places = useAsync(() => api.travelPlaces(), [version])
  const [chosen, setChosen] = useState('')
  const [profile, setProfile] = useState('wagon')
  const vocabulary = useAsync(() => api.vocabulary(), [version])

  useEffect(() => {
    if (!chosen && places.data?.length) setChosen(places.data[0].id)
  }, [places.data, chosen])

  const report = useAsync(
    () => (chosen ? api.supply(chosen, day, profile) : Promise.resolve(null)),
    [chosen, day, profile, version],
  )

  if (places.loading) return <Loading />
  if (places.error) return <ErrorBox error={places.error} />

  return (
    <>
      <Panel title="Where does it come from?">
        <div className="toolbar">
          <label>
            <div className="small muted">Place</div>
            <select value={chosen} onChange={(e) => setChosen(e.target.value)}>
              {(places.data ?? []).map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
          <label>
            <div className="small muted">Goods travel by</div>
            <select value={profile} onChange={(e) => setProfile(e.target.value)}>
              {(vocabulary.data?.transport_profiles ?? []).map((t) => (
                <option key={t.key} value={t.key}>{t.label}</option>
              ))}
            </select>
          </label>
        </div>
        <p className="muted small">
          On {dateText}. Seasonal closures and the dates roads were built both apply, so
          a supply line can be open in summer and gone in winter.
        </p>
      </Panel>

      {report.loading && <Loading what="Tracing the supply" />}
      {report.error && <ErrorBox error={report.error} />}

      {report.data && (
        <>
          {report.data.needs.length === 0 && (
            <Panel title={`${report.data.place_name} asks for nothing`}>
              <p className="muted small">
                Nothing is recorded as imported or consumed here. Record what a place
                needs — “Greyhaven imports Grain” — and the supply path appears.
              </p>
            </Panel>
          )}

          {report.data.needs.map((need) => (
            <SupplyPanel key={need.resource_id} need={need} onSelect={onSelect} />
          ))}

          {report.data.depended_on_by.length > 0 && (
            <Panel title="Who leans on this place"
                   count={report.data.depended_on_by.length}>
              <Findings rows={report.data.depended_on_by} onSelect={onSelect} />
            </Panel>
          )}

          {report.data.standing.length > 0 && (
            <Panel title="What that is worth">
              <p className="muted small">
                Counted from what they hold, rather than read off a label somebody typed
                (§86).
              </p>
              <Findings rows={report.data.standing} onSelect={onSelect} />
            </Panel>
          )}
        </>
      )}
    </>
  )
}

function SupplyPanel({ need, onSelect }:
  { need: SupplyNeed; onSelect: (id: string) => void }) {
  return (
    <Panel title={need.resource_name}
           count={need.level ? undefined : need.sources.length}>
      {need.level && (
        <p className="small muted">
          Needed here: <strong>{need.level.replace(/_/g, ' ')}</strong>
        </p>
      )}

      <Findings rows={need.findings} onSelect={onSelect} />

      {need.sources.length > 0 && (
        <div className="scroll-x" style={{ marginTop: 8 }}>
          <table className="data">
            <thead>
              <tr>
                <th>From</th><th>Has</th><th>Days</th><th>The way it comes</th>
              </tr>
            </thead>
            <tbody>
              {need.sources.map((s) => (
                <tr key={s.entity_id}>
                  <td>
                    <button className="link" onClick={() => onSelect(s.entity_id)}>
                      {s.name}
                    </button>
                    {s.exports && <Badge>exports</Badge>}
                  </td>
                  <td className="small">{s.level.replace(/_/g, ' ') || '—'}</td>
                  <td className="mono">
                    {s.days ?? <span className="muted">—</span>}
                  </td>
                  <td className="small">
                    {s.path_names.length
                      ? s.path_names.join(' → ')
                      : <span className="muted">{s.note}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

/** §67: every derived line shows the fact it was drawn from. */
function Findings({ rows, onSelect }:
  { rows: Finding[]; onSelect: (id: string) => void }) {
  return (
    <ul className="clean">
      {rows.map((f, i) => (
        <li key={i} className="supply-finding">
          <div>
            {ALARM[f.kind] && <Badge kind={ALARM[f.kind]}>{f.kind}</Badge>}{' '}
            {f.text}
          </div>
          {f.evidence.map((e, j) => (
            <div key={j} className="small muted">{e}</div>
          ))}
          {f.entity_ids.length > 0 && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 2 }}>
              {f.entity_ids.slice(0, 6).map((id, k) => (
                (f.entity_names?.[k] ?? '') && (
                  <button key={id} className="link small"
                          onClick={() => onSelect(id)}>{f.entity_names?.[k]}</button>
                )
              ))}
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}
