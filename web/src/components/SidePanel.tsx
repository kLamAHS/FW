/**
 * The contextual side panel (§76).
 *
 * "Wherever the user is working, allow quick inspection without leaving context." Clicking
 * a house on the map, a node in the graph or a name in a scene opens this rather than
 * navigating away — the writer keeps their place.
 *
 * It also carries §51's "why does this matter" and §52's "what changes if this
 * disappears", because the answer to both is nearly always wanted about the thing you have
 * just clicked.
 */

import { useState } from 'react'
import { api } from '../api'
import type { Finding } from '../api'
import { Badge, ErrorBox, Loading, TypeChip, useAsync } from './common'

interface Props {
  entityId: string
  day: number
  onClose: () => void
  onSelect: (id: string) => void
}

type Tab = 'facts' | 'why' | 'impact'

export function SidePanel({ entityId, day, onClose, onSelect }: Props) {
  const [tab, setTab] = useState<Tab>('facts')
  const bundle = useAsync(() => api.entity(entityId, day), [entityId, day])

  if (bundle.loading) return <aside className="side"><Loading /></aside>
  if (bundle.error) return <aside className="side"><ErrorBox error={bundle.error} /></aside>
  if (!bundle.data) return null

  const { entity, facts, events, titles, knowledge, scenes } = bundle.data

  return (
    <aside className="side" aria-label={`Details for ${entity.name}`}>
      <button className="close" onClick={onClose} aria-label="Close panel">✕</button>
      <h2>{entity.name}</h2>
      <div style={{ marginBottom: 10 }}>
        <TypeChip type={entity.type_key} />
        {entity.confidence !== 'canon' && <Badge kind="disputed">{entity.confidence}</Badge>}
      </div>

      {entity.summary && <p className="serif">{entity.summary}</p>}

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
                <div key={f.id + f.predicate_key} className="fact-line">
                  <span className="pred">{f.predicate_label}</span>
                  {f.object_id ? (
                    <button className="obj" style={{ border: 0, background: 'none', padding: 0 }}
                            onClick={() => onSelect(f.object_id!)}>
                      {f.object_name}
                    </button>
                  ) : (
                    <span className="obj">{f.value}</span>
                  )}
                  {f.strength && <Badge>{f.strength.replace(/_/g, ' ')}</Badge>}
                  {f.is_secret && <Badge kind="secret" title="Secret">secret</Badge>}
                  {f.note && <div className="small muted" style={{ width: '100%' }}>{f.note}</div>}
                </div>
              ))}
            </Section>
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
        </>
      )}

      {tab === 'why' && <WhyTab entityId={entityId} day={day} />}
      {tab === 'impact' && <ImpactTab entityId={entityId} day={day} />}
    </aside>
  )
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
