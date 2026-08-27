/**
 * The contextual side panel (§76) — now also where editing happens.
 *
 * "Wherever the user is working, allow quick inspection without leaving context." Editing
 * obeys the same rule: renaming a character or recording a new relationship happens here,
 * in place, rather than on a separate admin page that loses the writer's position.
 *
 * Two editing decisions worth naming:
 * - **Ending a fact uses the timeline's current date.** The slider already answers "when
 *   is now?", so "this stopped being true" is one click, and §106.3's rule — close
 *   history, never overwrite it — becomes the easy path rather than the disciplined one.
 * - **Deletion is a two-step button, not a confirm dialog.** The first click arms it and
 *   says what it will do; the second does it. Nothing modal, nothing to dismiss.
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { CalendarInfo, Fact, Finding, Vocabulary } from '../api'
import { Badge, ErrorBox, Loading, TypeChip, useAsync } from './common'
import { EntityForm, FactForm } from './forms'

interface Props {
  entityId: string
  day: number
  dateText: string
  vocabulary: Vocabulary | null
  calendar: CalendarInfo
  onClose: () => void
  onSelect: (id: string) => void
  onMutate: () => void
}

type Tab = 'facts' | 'why' | 'impact'
type Mode = 'view' | 'edit' | 'add-fact'

export function SidePanel(
  { entityId, day, dateText, vocabulary, calendar, onClose, onSelect, onMutate }: Props,
) {
  const [tab, setTab] = useState<Tab>('facts')
  const [mode, setMode] = useState<Mode>('view')
  const [armedDelete, setArmedDelete] = useState(false)
  const bundle = useAsync(() => api.entity(entityId, day), [entityId, day])

  // A new selection starts fresh: on the details tab, in view mode, with nothing armed.
  // Without this, selecting a character while the panel sits on "If it vanished" opens
  // them mid-analysis — and a just-created entity would land on a tab about its removal.
  useEffect(() => {
    setTab('facts')
    setMode('view')
    setArmedDelete(false)
  }, [entityId])

  if (bundle.loading) return <aside className="side"><Loading /></aside>
  if (bundle.error) return <aside className="side"><ErrorBox error={bundle.error} /></aside>
  if (!bundle.data) return null

  const { entity, facts, events, titles, knowledge, scenes } = bundle.data

  const changed = () => {
    bundle.reload()
    onMutate()
  }

  const removeEntity = async () => {
    await api.deleteEntity(entity.id)
    onMutate()
    onClose()
  }

  return (
    <aside className="side" aria-label={`Details for ${entity.name}`}>
      <button className="close" onClick={onClose} aria-label="Close panel">✕</button>
      <h2>{entity.name}</h2>
      <div style={{ marginBottom: 10 }}>
        <TypeChip type={entity.type_key} />
        {entity.confidence !== 'canon' && <Badge kind="disputed">{entity.confidence}</Badge>}
      </div>

      {mode === 'edit' && vocabulary ? (
        <EntityForm
          vocabulary={vocabulary}
          calendar={calendar}
          existing={entity}
          onDone={() => { setMode('view'); changed() }}
          onCancel={() => setMode('view')}
        />
      ) : mode === 'add-fact' && vocabulary ? (
        <FactForm
          subject={entity}
          vocabulary={vocabulary}
          calendar={calendar}
          onDone={() => { setMode('view'); changed() }}
          onCancel={() => setMode('view')}
        />
      ) : (
        <>
          {entity.summary && <p className="serif">{entity.summary}</p>}

          <div className="toolbar">
            <button onClick={() => setMode('edit')} disabled={!vocabulary}
                    title={vocabulary ? undefined
                      : 'The vocabulary failed to load — reload the page'}>
              Edit
            </button>
            <button onClick={() => setMode('add-fact')} disabled={!vocabulary}
                    title={vocabulary ? undefined
                      : 'The vocabulary failed to load — reload the page'}>
              Add a connection
            </button>
            <span className="spacer" />
            {armedDelete ? (
              <>
                <button className="danger" onClick={() => void removeEntity()}>
                  Really delete
                </button>
                <button onClick={() => setArmedDelete(false)}>Keep it</button>
              </>
            ) : (
              <button className="danger" onClick={() => setArmedDelete(true)}
                      title="Removes the entity and every fact touching it">
                Delete
              </button>
            )}
          </div>

          <div className="toolbar" role="tablist">
            <button role="tab" aria-selected={tab === 'facts'}
                    className={tab === 'facts' ? 'active' : ''}
                    onClick={() => setTab('facts')}>Details</button>
            <button role="tab" aria-selected={tab === 'why'}
                    className={tab === 'why' ? 'active' : ''}
                    onClick={() => setTab('why')}>Why it matters</button>
            <button role="tab" aria-selected={tab === 'impact'}
                    className={tab === 'impact' ? 'active' : ''}
                    onClick={() => setTab('impact')}>If it vanished</button>
          </div>

          {tab === 'facts' && (
            <>
              {titles.length > 0 && (
                <Section title="Titles held">
                  <ul className="clean">
                    {titles.map((t) => <li key={t.id}>{t.name}</li>)}
                  </ul>
                </Section>
              )}

              {facts.length > 0 && (
                <Section title="Connections" count={facts.length}>
                  {facts.map((f) => (
                    <FactLine key={f.id + f.predicate_key} fact={f}
                              day={day} dateText={dateText}
                              onSelect={onSelect} onChanged={changed} />
                  ))}
                </Section>
              )}
              {facts.length === 0 && (
                <p className="muted small">
                  Nothing recorded yet — “Add a connection” is where a world starts.
                </p>
              )}

              {knowledge.length > 0 && (
                <Section title="What they know">
                  <ul className="clean">
                    {knowledge.map((k, i) => (
                      <li key={i} className="small">
                        <strong>{k.stance}</strong>{' '}
                        {k.about_observer_id ? 'that another knows ' : ''}
                        “{k.secret_name}”
                        {k.note && <div className="muted">{k.note}</div>}
                      </li>
                    ))}
                  </ul>
                </Section>
              )}

              {events.length > 0 && (
                <Section title="History" count={events.length}>
                  <ul className="clean">
                    {events.map((e) => <li key={e.id} className="small">{e.name}</li>)}
                  </ul>
                </Section>
              )}

              {scenes.length > 0 && (
                <Section title="Appears in">
                  <ul className="clean">
                    {scenes.map((s) => <li key={s.id} className="small">{s.title}</li>)}
                  </ul>
                </Section>
              )}

              <ChangeHistory entityId={entity.id} onChanged={changed} />
            </>
          )}

          {tab === 'why' && <WhyTab entityId={entityId} day={day} />}
          {tab === 'impact' && <ImpactTab entityId={entityId} day={day} />}
        </>
      )}
    </aside>
  )
}

/** One fact row, with its quiet end/delete affordances. */
function FactLine(
  { fact, day, dateText, onSelect, onChanged }:
  {
    fact: Fact
    day: number
    dateText: string
    onSelect: (id: string) => void
    onChanged: () => void
  },
) {
  const [armed, setArmed] = useState<'end' | 'delete' | null>(null)
  // An incoming fact is shown flipped through its inverse; ending or deleting it must
  // target the real row, which fact.id already names in either direction.
  const alreadyEnded = fact.valid_to !== null && fact.valid_to <= day

  const endIt = async () => {
    await api.endFact(fact.id, day)
    setArmed(null)
    onChanged()
  }
  const deleteIt = async () => {
    await api.deleteFact(fact.id)
    setArmed(null)
    onChanged()
  }

  return (
    <div className="fact-line">
      <span className="pred">{fact.predicate_label}</span>
      {fact.object_id ? (
        <button className="obj" style={{ border: 0, background: 'none', padding: 0 }}
                onClick={() => onSelect(fact.object_id!)}>
          {fact.object_name}
        </button>
      ) : (
        <span className="obj">{fact.value}</span>
      )}
      {fact.strength && <Badge>{fact.strength.replace(/_/g, ' ')}</Badge>}
      {fact.is_secret && <Badge kind="secret" title="Secret">secret</Badge>}

      <span className="fact-actions">
        {armed === 'end' ? (
          <>
            <button className="danger" onClick={() => void endIt()}
                    title={`Close this fact's validity on ${dateText}`}>
              end on {dateText}
            </button>
            <button onClick={() => setArmed(null)}>keep</button>
          </>
        ) : armed === 'delete' ? (
          <>
            <button className="danger" onClick={() => void deleteIt()}>really delete</button>
            <button onClick={() => setArmed(null)}>keep</button>
          </>
        ) : (
          <>
            {!alreadyEnded && (
              <button onClick={() => setArmed('end')}
                      title="It stopped being true — close it on the current date">
                end
              </button>
            )}
            <button onClick={() => setArmed('delete')}
                    title="It was never true — remove the entry">
              ✕
            </button>
          </>
        )}
      </span>

      {fact.note && <div className="small muted" style={{ width: '100%' }}>{fact.note}</div>}
    </div>
  )
}

/** §59: the entity's own change history, collapsed by default, each step reversible. */
function ChangeHistory(
  { entityId, onChanged }: { entityId: string; onChanged: () => void },
) {
  const [open, setOpen] = useState(false)
  const [restoreError, setRestoreError] = useState<string | null>(null)
  // A restore appends its own inverse record, so the list it was clicked in is stale
  // the moment it succeeds — refetch, or the writer can click the same restore twice.
  const [generation, setGeneration] = useState(0)
  const history = useAsync(
    () => (open ? api.history(entityId) : Promise.resolve(null)),
    [open, entityId, generation],
  )

  const restore = async (revisionId: number) => {
    setRestoreError(null)
    try {
      await api.restoreRevision(revisionId)
      setGeneration((g) => g + 1)
      onChanged()
    } catch (err) {
      setRestoreError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div style={{ marginTop: 14 }}>
      <button className="disclosure" onClick={() => setOpen((v) => !v)}
              aria-expanded={open}>
        {open ? '▾' : '▸'} Change history
      </button>
      {open && history.loading && <Loading />}
      {open && restoreError && <div className="error-box small">{restoreError}</div>}
      {open && history.data && (
        <ul className="clean small">
          {history.data.map((r) => (
            <li key={r.id} style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
              {/* r.at carries its UTC offset; Date renders it in the writer's zone. */}
              <span className="mono muted">{new Date(r.at).toLocaleString()}</span>
              <span style={{ flex: 1 }}>{describeRevision(r)}</span>
              {r.action === 'update' && (
                <button className="small" title="Put the earlier values back"
                        onClick={() => void restore(r.id)}>
                  restore
                </button>
              )}
            </li>
          ))}
          {history.data.length === 0 && <li className="muted">No recorded changes.</li>}
        </ul>
      )}
    </div>
  )
}

function describeRevision(r: {
  action: string
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
}): string {
  if (r.action === 'insert') return 'created'
  if (r.action === 'delete') return 'deleted'
  const changes = Object.entries(r.after ?? {})
    .map(([key, value]) => `${key.replace(/_/g, ' ')} → ${String(value ?? '—')}`)
  return changes.length ? changes.join(', ') : 'changed'
}

function Section(
  { title, count, children }:
  { title: string; count?: number; children: React.ReactNode },
) {
  return (
    <div style={{ marginTop: 14 }}>
      <h3 style={{ marginBottom: 5 }}>
        {title} {count !== undefined && <span className="muted small">({count})</span>}
      </h3>
      {children}
    </div>
  )
}

function WhyTab({ entityId, day }: { entityId: string; day: number }) {
  const { data, error, loading } = useAsync(() => api.why(entityId, day), [entityId, day])
  if (loading) return <Loading what="Working out why this matters" />
  if (error) return <ErrorBox error={error} />
  if (!data?.findings.length) {
    return <p className="muted small">Nothing in the model marks this as important yet.</p>
  }
  return <FindingList findings={data.findings} note={data.note} />
}

function ImpactTab({ entityId, day }: { entityId: string; day: number }) {
  const { data, error, loading } = useAsync(() => api.impact(entityId, day), [entityId, day])
  if (loading) return <Loading what="Tracing what depends on this" />
  if (error) return <ErrorBox error={error} />
  if (!data?.consequences.length) {
    return <p className="muted small">Nothing in the model depends on this.</p>
  }
  return <FindingList findings={data.consequences} note={data.note} />
}

/** §67: every derived claim shows the reasoning that produced it. */
function FindingList({ findings, note }: { findings: Finding[]; note: string }) {
  return (
    <div style={{ marginTop: 10 }}>
      {findings.map((f, i) => (
        <div key={i} className="finding">
          <div>
            {f.kind === 'authored'
              ? <Badge title="Written by you">authored</Badge>
              : <Badge title="Worked out from the world model">derived</Badge>}{' '}
            {f.text}
          </div>
          {f.evidence.map((line, j) => (
            <div key={j} className="evidence">{line}</div>
          ))}
        </div>
      ))}
      <p className="muted small" style={{ marginTop: 10 }}>{note}</p>
    </div>
  )
}
