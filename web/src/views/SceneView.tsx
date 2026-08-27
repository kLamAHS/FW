/**
 * Scene writing context (§44, §45, §96).
 *
 * The panel the brief describes in the most detail, and the clearest statement of what the
 * whole application is for: "The writer should not need to remember these facts manually."
 *
 * Ordering matters here. Tensions come first because they answer the question a writer
 * actually has when opening a scene — why is this conversation difficult? — and the raw
 * relationships and secrets follow as the evidence behind it.
 */

import { useState } from 'react'
import { api } from '../api'
import type { CalendarInfo } from '../api'
import { Badge, ErrorBox, Loading, Panel, useAsync } from '../components/common'
import { Modal, SceneForm } from '../components/forms'

interface Props {
  onSelect: (id: string) => void
  version: number
  calendar: CalendarInfo
  onMutate: () => void
}

export function SceneView({ onSelect, version, calendar, onMutate }: Props) {
  const scenes = useAsync(() => api.scenes(), [version])
  const [chosen, setChosen] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const sceneId = chosen ?? scenes.data?.[0]?.id ?? null
  const context = useAsync(
    () => (sceneId ? api.sceneContext(sceneId) : Promise.resolve(null)),
    [sceneId, version],
  )

  if (scenes.loading) return <Loading />
  if (scenes.error) return <ErrorBox error={scenes.error} />

  const creator = creating && (
    <Modal title="A new scene" onClose={() => setCreating(false)}>
      <SceneForm
        calendar={calendar}
        onDone={() => { setCreating(false); onMutate() }}
        onCancel={() => setCreating(false)}
      />
    </Modal>
  )

  if (!scenes.data?.length) {
    return (
      <>
        <p className="muted">
          This world has no scenes yet.{' '}
          <button onClick={() => setCreating(true)}>Write the first one</button>
        </p>
        {creator}
      </>
    )
  }

  return (
    <>
      <div className="toolbar">
        <span className="small muted">Scene</span>
        {scenes.data.map((s) => (
          <button key={s.id} className={s.id === sceneId ? 'active' : ''}
                  onClick={() => setChosen(s.id)}>
            {s.title}
          </button>
        ))}
        <span className="spacer" />
        <button onClick={() => setCreating(true)}>+ New scene</button>
      </div>
      {creator}

      {context.loading && <Loading what="Gathering what matters here" />}
      {context.error ? <ErrorBox error={context.error} /> : null}
      {context.data && (
        <>
          <Panel>
            <h1 className="serif">{context.data.title}</h1>
            <p className="mono small muted">
              {context.data.date_text}
              {context.data.location && ` · ${context.data.location.name}`}
            </p>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
              <span className="small muted">Present:</span>
              {context.data.participants.map((p) => (
                <button key={p.id} className="entity-line"
                        style={{ width: 'auto', padding: '2px 8px' }}
                        onClick={() => onSelect(p.id)}>
                  <span className="name">{p.name}</span>
                </button>
              ))}
            </div>
            {context.data.world_state_notes.length > 0 && (
              <ul className="clean small muted" style={{ marginTop: 10 }}>
                {context.data.world_state_notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            )}
          </Panel>

          {context.data.tensions.length > 0 && (
            <Panel title="Why this room is tense">
              {context.data.tensions.map((t, i) => (
                <div className="tension" key={i}>{t}</div>
              ))}
            </Panel>
          )}

          <div className="grid wide">
            <Panel title="Relevant relationships"
                   count={context.data.relationships.length}>
              <ul className="clean">
                {context.data.relationships.map((r, i) => (
                  <li key={i}>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'baseline',
                                  flexWrap: 'wrap' }}>
                      <button className="entity-line" style={{ width: 'auto', padding: 0 }}
                              onClick={() => onSelect(r.subject_id)}>
                        <span className="name">{r.subject}</span>
                      </button>
                      <span className="muted">
                        {r.strength ? r.strength.replace(/_/g, ' ') : r.predicate.replace(/_/g, ' ')}
                      </span>
                      {r.object_id && (
                        <button className="entity-line" style={{ width: 'auto', padding: 0 }}
                                onClick={() => onSelect(r.object_id)}>
                          <span className="name">{r.object}</span>
                        </button>
                      )}
                      {r.secret && <Badge kind="secret">secret</Badge>}
                    </div>
                    {r.note && <div className="small muted">{r.note}</div>}
                  </li>
                ))}
              </ul>
              <p className="muted small" style={{ marginTop: 8 }}>
                Ranked by relevance to this room, not listed in full — both parties being
                present counts for most.
              </p>
            </Panel>

            <Panel title="Relevant secrets" count={context.data.secrets.length}>
              <ul className="clean">
                {context.data.secrets.map((s, i) => (
                  <li key={i}>
                    <div>{s.text}</div>
                    {s.note && <div className="small muted">{s.note}</div>}
                  </li>
                ))}
              </ul>
            </Panel>

            <Panel title="Active goals" count={context.data.goals.length}>
              <ul className="clean">
                {context.data.goals.map((g, i) => (
                  <li key={i}>
                    <button className="entity-line" style={{ width: 'auto', padding: 0 }}
                            onClick={() => onSelect(g.person_id)}>
                      <span className="name">{g.person}</span>
                    </button>{' '}
                    <span className="muted">
                      {g.kind === 'private_goal' ? 'privately wants' : 'openly wants'}
                    </span>{' '}
                    {g.text}
                  </li>
                ))}
              </ul>
            </Panel>

            <Panel title="Recent history" count={context.data.recent_events.length}>
              <ul className="clean">
                {context.data.recent_events.map((e) => (
                  <li key={e.id}>
                    <strong>{e.name}</strong>{' '}
                    <span className="muted small">
                      {e.days_ago === 0 ? 'earlier the same day'
                        : `${e.days_ago} days earlier`}
                    </span>
                    {e.summary && <div className="small">{e.summary}</div>}
                  </li>
                ))}
                {context.data.recent_events.length === 0 && (
                  <li className="muted">Nothing recent involves anyone here.</li>
                )}
              </ul>
            </Panel>
          </div>
        </>
      )}
    </>
  )
}
