/**
 * Browsing entities (§54's settlement list and faction views), the timeline (§31), and
 * continuity (§46).
 *
 * Three small views kept together because each is a list with one idea. The continuity
 * list is the one with a real interaction: §46 requires intentional exceptions, so every
 * finding can be dismissed with a reason, and the reason is required rather than optional
 * — a suppression with no explanation is a bug waiting to be rediscovered.
 */

import { useState } from 'react'
import { api } from '../api'
import type { CalendarInfo, Vocabulary, WorldSummary } from '../api'
import { EventForm, Modal } from '../components/forms'
import {
  Badge, ErrorBox, Loading, Panel, SEVERITY_GLYPH, TypeChip, useAsync,
} from '../components/common'

/* ------------------------------------------------------------------ entities */

export function EntitiesView(
  { world, day, onSelect, version, vocabulary }:
  { world: WorldSummary; day: number; onSelect: (id: string) => void
    version: number; vocabulary: Vocabulary | null },
) {
  const [type, setType] = useState<string>('')
  const [atDate, setAtDate] = useState(true)
  // On by default (§66). A map proposes towns and castles by the dozen, and this list
  // is where a writer looks for the ones *they* wrote; what they have accepted is
  // theirs and stays, tag or no tag.
  const [hideProposed, setHideProposed] = useState(true)
  const { data, error, loading } = useAsync(
    () => api.entities({
      type_key: type || undefined, at: atDate ? day : undefined,
      hide_generated: hideProposed || undefined,
    }),
    [type, atDate, day, version, hideProposed],
  )

  const types = (vocabulary?.entity_types ?? [])
    .filter((t) => world.counts[t.key])
    .sort((a, b) => (world.counts[b.key] ?? 0) - (world.counts[a.key] ?? 0))

  return (
    <>
      <div className="toolbar">
        <button className={type === '' ? 'active' : ''} onClick={() => setType('')}>
          everything
        </button>
        {types.map((t) => (
          <button key={t.key} className={type === t.key ? 'active' : ''}
                  onClick={() => setType(t.key)}>
            {t.plural.toLowerCase()} <span className="muted">{world.counts[t.key]}</span>
          </button>
        ))}
        <span className="spacer" />
        <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={atDate} onChange={() => setAtDate((v) => !v)} />
          <span className="small">only what exists on this date</span>
        </label>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={hideProposed}
                 onChange={() => setHideProposed((v) => !v)} />
          <span className="small">hide what the map has only suggested</span>
        </label>
      </div>

      {loading && <Loading />}
      {error && <ErrorBox error={error} />}
      <Panel count={data?.length ?? 0} title="Entities">
        {(data ?? []).map((e) => (
          <button key={e.id} className="entity-line" onClick={() => onSelect(e.id)}>
            <span className="name">{e.name}</span>
            <TypeChip type={e.type_key} />
            <span className="desc">{e.summary}</span>
            {e.confidence !== 'canon' && <Badge kind="disputed">{e.confidence}</Badge>}
          </button>
        ))}
        {data?.length === 0 && <p className="muted">Nothing here on this date.</p>}
      </Panel>
    </>
  )
}

/* ------------------------------------------------------------------ timeline */

export function EventsView(
  { day, onSelect, version, calendar, onMutate }:
  { day: number; onSelect: (id: string) => void; version: number
    calendar: CalendarInfo; onMutate: () => void },
) {
  const { data, error, loading } = useAsync(() => api.events(), [version])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [causePick, setCausePick] = useState('')
  const [linkError, setLinkError] = useState<string | null>(null)
  const consequences = useAsync(
    () => (expanded ? api.consequences(expanded) : Promise.resolve([])),
    [expanded, version],
  )

  if (loading) return <Loading />
  if (error) return <ErrorBox error={error} />

  // One picker state serves whichever event is expanded, so switching events must
  // not carry a stale selection (or a stale refusal) across.
  const expand = (id: string | null) => {
    setExpanded(id)
    setCausePick('')
    setLinkError(null)
  }

  const recordConsequence = async (causeId: string) => {
    if (!causePick) return
    setLinkError(null)
    try {
      await api.linkCause(causeId, causePick)
      setCausePick('')
      onMutate()
    } catch (err) {
      // A refused link (a causal loop, a vanished event) must be read, not lost.
      setLinkError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <>
      <div className="toolbar">
        <span className="spacer" />
        <button onClick={() => setCreating(true)}>+ New event</button>
      </div>
      {creating && (
        <Modal title="Record what happened" onClose={() => setCreating(false)}>
          <EventForm
            calendar={calendar}
            onDone={() => { setCreating(false); onMutate() }}
            onCancel={() => setCreating(false)}
          />
        </Modal>
      )}
      <Panel title="History" count={data?.length ?? 0}>
      <ul className="clean">
        {(data ?? []).map((e) => {
          const future = e.start_day !== null && e.start_day > day
          return (
            <li key={e.id} style={{ opacity: future ? 0.5 : 1 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline',
                            flexWrap: 'wrap' }}>
                <span className="mono small muted" style={{ minWidth: '16ch' }}>
                  {e.date_text}
                </span>
                <strong>{e.name}</strong>
                <TypeChip type={e.type_key} />
                {future && <Badge>has not happened yet</Badge>}
                <span className="spacer" style={{ flex: 1 }} />
                <button className="small"
                        onClick={() => expand(expanded === e.id ? null : e.id)}>
                  {expanded === e.id ? 'hide' : 'consequences'}
                </button>
              </div>
              {e.summary && <div className="small">{e.summary}</div>}
              {e.participants.length > 0 && (
                <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 3 }}>
                  {e.participants.map((p) => (
                    <button key={p.id + p.role} className="entity-line"
                            style={{ width: 'auto', padding: '1px 6px' }}
                            onClick={() => onSelect(p.id)}>
                      <span className="name small">{p.name}</span>
                      <span className="muted small">{p.role}</span>
                    </button>
                  ))}
                </div>
              )}
              {expanded === e.id && (
                <div style={{ marginTop: 8, paddingLeft: 14,
                              borderLeft: '2px solid var(--accent)' }}>
                  {consequences.loading && <Loading what="Tracing" />}
                  {(consequences.data ?? []).length === 0 && !consequences.loading && (
                    <p className="muted small">Nothing is recorded as following from this.</p>
                  )}
                  {(consequences.data ?? []).map((c) => (
                    <div key={c.id} className="small">
                      {'→ '.repeat(c.depth)}{c.name}
                    </div>
                  ))}
                  {/* §32: record that this event led to another. */}
                  <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                    <select value={causePick}
                            onChange={(ev) => setCausePick(ev.target.value)}
                            aria-label="This event led to…">
                      <option value="">this led to…</option>
                      {(data ?? [])
                        .filter((other) => other.id !== e.id)
                        .map((other) => (
                          <option key={other.id} value={other.id}>{other.name}</option>
                        ))}
                    </select>
                    <button disabled={!causePick}
                            onClick={() => void recordConsequence(e.id)}>
                      Record
                    </button>
                  </div>
                  {linkError && <div className="error-box small">{linkError}</div>}
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </Panel>
    </>
  )
}

/* ------------------------------------------------------------------ continuity */

export function ContinuityView(
  { onSelect, version }: { onSelect: (id: string) => void; version: number },
) {
  const [minimum, setMinimum] = useState('notice')
  const report = useAsync(() => api.continuity(minimum), [minimum, version])
  const [dismissing, setDismissing] = useState<string | null>(null)
  const [reason, setReason] = useState('')

  const dismiss = async (rule_key: string, fingerprint: string) => {
    await api.suppress(rule_key, fingerprint, reason)
    setDismissing(null)
    setReason('')
    report.reload()
  }

  return (
    <>
      <div className="toolbar">
        <span className="small muted">Show</span>
        {['notice', 'warning', 'error'].map((level) => (
          <button key={level} className={minimum === level ? 'active' : ''}
                  onClick={() => setMinimum(level)}>
            {level === 'notice' ? 'everything'
              : level === 'warning' ? 'warnings and errors' : 'errors only'}
          </button>
        ))}
      </div>

      {report.loading && <Loading what="Checking the world" />}
      {report.error ? <ErrorBox error={report.error} /> : null}
      {report.data && (
        <Panel title="Continuity" count={report.data.summary}>
          {report.data.violations.length === 0 && (
            <p className="muted">
              Nothing to report. {report.data.rules_run} checks ran
              {report.data.suppressed > 0
                && `, and ${report.data.suppressed} known exception${
                  report.data.suppressed === 1 ? ' was' : 's were'} skipped`}.
            </p>
          )}
          {report.data.violations.map((v) => (
            <div key={v.fingerprint} className={`violation ${v.severity}`}>
              <span className="glyph" title={v.severity}>{SEVERITY_GLYPH[v.severity]}</span>
              <div style={{ flex: 1 }}>
                <div>{v.message}</div>
                <div className="small muted">
                  {v.rule_key.replace(/_/g, ' ')}
                  {v.detail && ` · ${v.detail}`}
                </div>
                {v.entity_ids.length > 0 && (
                  <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 3 }}>
                    {v.entity_ids.map((id) => (
                      <button key={id} className="entity-line small"
                              style={{ width: 'auto', padding: '1px 6px' }}
                              onClick={() => onSelect(id)}>
                        inspect
                      </button>
                    ))}
                  </div>
                )}
                {dismissing === v.fingerprint ? (
                  <div style={{ marginTop: 6, display: 'flex', gap: 6 }}>
                    <input
                      autoFocus
                      placeholder="Why is this intentional?"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      style={{ flex: 1 }}
                    />
                    <button disabled={!reason.trim()}
                            onClick={() => dismiss(v.rule_key, v.fingerprint)}>
                      Dismiss
                    </button>
                    <button onClick={() => { setDismissing(null); setReason('') }}>
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button className="small" style={{ marginTop: 4 }}
                          onClick={() => setDismissing(v.fingerprint)}>
                    This is intentional
                  </button>
                )}
              </div>
            </div>
          ))}
        </Panel>
      )}
    </>
  )
}
