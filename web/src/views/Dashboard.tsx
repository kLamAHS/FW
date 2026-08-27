/**
 * The world dashboard (§74).
 *
 * "The home screen should summarize the current world." The sections the brief lists are
 * the ones that answer *where is my story right now* — the major powers, the current
 * conflicts, the unresolved succession, the recent events, and the warnings.
 *
 * The unresolved-succession block is the one that earns its place: it computes the line
 * for every title on the current date, so an heirless or contested crown surfaces on the
 * home screen rather than waiting to be looked for.
 */

import { api } from '../api'
import type { WorldSummary } from '../api'
import { Badge, ErrorBox, Loading, Panel, SEVERITY_GLYPH, useAsync } from '../components/common'

interface Props {
  world: WorldSummary
  day: number
  dateText: string
  onSelect: (id: string) => void
  onGo: (view: string) => void
}

export function Dashboard({ world, day, dateText, onSelect, onGo }: Props) {
  const titles = useAsync(() => api.titles(day), [day])
  const continuity = useAsync(() => api.continuity(), [])
  const events = useAsync(() => api.events(), [])
  const state = useAsync(() => api.state(day), [day])
  const secrets = useAsync(() => api.secrets(day), [day])

  const succession = useAsync(async () => {
    const list = await api.titles(day)
    return Promise.all(
      list.map(async (t) => ({ title: t, line: await api.succession(t.id, { day }) })),
    )
  }, [day])

  const recent = (events.data ?? [])
    .filter((e) => e.start_day !== null && e.start_day <= day)
    .sort((a, b) => (b.start_day ?? 0) - (a.start_day ?? 0))
    .slice(0, 6)

  const wars = (state.data?.facts ?? []).filter((f) => f.predicate_key === 'at_war_with')
  const claims = (state.data?.facts ?? []).filter((f) => f.predicate_key === 'claims')

  return (
    <>
      <Panel>
        <h1 className="serif" style={{ marginBottom: 4 }}>{world.name}</h1>
        <p className="muted">{world.description}</p>
        <p className="mono small">{dateText}</p>
        <div className="stat-row" style={{ marginTop: 12 }}>
          {['person', 'settlement', 'house', 'region', 'facts', 'events', 'scenes']
            .filter((k) => world.counts[k])
            .map((k) => (
              <div className="stat" key={k}>
                <div className="n">{world.counts[k]}</div>
                <div className="k">{k === 'person' ? 'people' : k}</div>
              </div>
            ))}
        </div>
      </Panel>

      <div className="grid wide">
        <Panel title="Who holds what" count={titles.data?.length ?? ''}
               actions={<button onClick={() => onGo('succession')}>Succession</button>}>
          {titles.loading && <Loading />}
          {titles.error ? <ErrorBox error={titles.error} /> : null}
          <ul className="clean">
            {(titles.data ?? []).map((t) => (
              <li key={t.id}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                  <strong>{t.name}</strong>
                  <span className="spacer" style={{ flex: 1 }} />
                  {t.holder ? (
                    <button className="entity-line" style={{ width: 'auto', padding: '2px 6px' }}
                            onClick={() => onSelect(t.holder!.id)}>
                      {t.holder.name}
                    </button>
                  ) : (
                    <Badge kind="error">vacant</Badge>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Unresolved succession"
               count={succession.data?.length ?? ''}>
          {succession.loading && <Loading what="Computing lines of succession" />}
          {(succession.data ?? []).map(({ title, line }) => (
            <div key={title.id} style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                <strong>{title.name}</strong>
                {!title.holder && <Badge kind="error">! no holder</Badge>}
                {line.line.length === 0 && <Badge kind="warning">△ no heir</Badge>}
              </div>
              {line.line.slice(0, 4).map((c) => (
                <div className="succession-line" key={c.id}>
                  <span className="pos">{c.position}</span>
                  <button className="entity-line" style={{ width: 'auto', padding: 0 }}
                          onClick={() => onSelect(c.id)}>
                    <span className="name">{c.name}</span>
                  </button>
                  {c.note && <span className="muted small">{c.note}</span>}
                </div>
              ))}
              <div className="muted small">under {line.law_label}</div>
            </div>
          ))}
        </Panel>

        <Panel title="Current conflicts">
          {wars.length === 0 && claims.length === 0 && (
            <p className="muted">No open wars or contested claims on this date.</p>
          )}
          {wars.map((f) => (
            <div key={f.id} className="fact-line">
              <Badge kind="error">! at war</Badge>
              <button className="entity-line" style={{ width: 'auto', padding: 0 }}
                      onClick={() => onSelect(f.subject_id)}>{f.subject_name}</button>
              <span className="muted">and</span>
              <button className="entity-line" style={{ width: 'auto', padding: 0 }}
                      onClick={() => onSelect(f.object_id!)}>{f.object_name}</button>
            </div>
          ))}
          {claims.map((f) => (
            <div key={f.id} className="fact-line">
              <Badge kind="warning">△ disputed</Badge>
              <button className="entity-line" style={{ width: 'auto', padding: 0 }}
                      onClick={() => onSelect(f.subject_id)}>{f.subject_name}</button>
              <span className="muted">claims</span>
              <button className="entity-line" style={{ width: 'auto', padding: 0 }}
                      onClick={() => onSelect(f.object_id!)}>{f.object_name}</button>
              {f.note && <div className="small muted" style={{ width: '100%' }}>{f.note}</div>}
            </div>
          ))}
        </Panel>

        <Panel title="Recent history"
               actions={<button onClick={() => onGo('timeline')}>Timeline</button>}>
          {events.loading && <Loading />}
          <ul className="clean">
            {recent.map((e) => (
              <li key={e.id}>
                <div><strong>{e.name}</strong></div>
                <div className="muted small mono">{e.date_text}</div>
                {e.summary && <div className="small">{e.summary}</div>}
              </li>
            ))}
            {recent.length === 0 && <li className="muted">Nothing has happened yet.</li>}
          </ul>
        </Panel>

        <Panel title="Secrets in play" count={secrets.data?.length ?? ''}>
          {(secrets.data ?? []).map((s) => (
            <div key={s.id} style={{ marginBottom: 10 }}>
              <strong>{s.name}</strong>
              {s.about && (
                <span className="muted"> — about {s.about.name}</span>
              )}
              <div className="small" style={{ marginTop: 3 }}>
                {Object.entries(s.by_stance).map(([stance, people]) => (
                  <div key={stance}>
                    <span className="muted">{stance}: </span>
                    {people.map((p) => p.about
                      ? `${p.name} (that ${p.about.name} knows)`
                      : p.name).join(', ')}
                  </div>
                ))}
              </div>
            </div>
          ))}
          {(secrets.data ?? []).length === 0 && (
            <p className="muted">No secrets recorded.</p>
          )}
        </Panel>

        <Panel title="Worldbuilding warnings"
               actions={<button onClick={() => onGo('continuity')}>All checks</button>}>
          {continuity.loading && <Loading />}
          {continuity.data && (
            <>
              <p className="small">{continuity.data.summary}</p>
              {continuity.data.violations.slice(0, 5).map((v, i) => (
                <div key={i} className={`violation ${v.severity}`}>
                  <span className="glyph">{SEVERITY_GLYPH[v.severity]}</span>
                  <span className="small">{v.message}</span>
                </div>
              ))}
            </>
          )}
        </Panel>
      </div>
    </>
  )
}
