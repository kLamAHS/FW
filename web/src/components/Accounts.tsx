/**
 * The same thing, said differently by different parties (§33, §94).
 *
 * A battle is a liberation to one house and a massacre to another; a man is the Prince to
 * one and the Pretender to the other. §33 asks for exactly this and the table has held it
 * since the first migration with nothing to read it, so the seeded world's three accounts
 * of the Red Ford were invisible and a writer could not record a fourth.
 *
 * Shown as a list of voices rather than a single "official" version, because the whole
 * point is that there is no official version — which is also what makes §94's perspective
 * possible: pick a holder and their line becomes the label on the map.
 */

import { useState } from 'react'
import { api } from '../api'
import type { Entity, Interpretation } from '../api'
import { EntityPicker } from './forms'
import { ErrorBox, Panel, useAsync } from './common'

interface Props {
  /** Exactly one of these, mirroring the row. */
  eventId?: string
  entityId?: string
  /** What to call the thing in the empty state — "this battle", "Prince Oren". */
  subject: string
  version: number
  onMutate: () => void
}

export function Accounts({ eventId, entityId, subject, version, onMutate }: Props) {
  const told = useAsync<Interpretation[]>(
    () => api.interpretations(eventId ? { event_id: eventId } : { entity_id: entityId }),
    [eventId, entityId, version],
  )
  const [adding, setAdding] = useState(false)
  const [holder, setHolder] = useState<Entity | null>(null)
  const [label, setLabel] = useState('')
  const [account, setAccount] = useState('')
  const [error, setError] = useState<string | null>(null)

  const keep = async () => {
    setError(null)
    try {
      await api.addInterpretation({
        label: label.trim(), account: account.trim(),
        event_id: eventId ?? null, entity_id: entityId ?? null,
        holder_id: holder?.id ?? null,
      })
      setLabel(''); setAccount(''); setHolder(null); setAdding(false)
      onMutate()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const drop = async (id: string) => {
    try {
      await api.forgetInterpretation(id)
      onMutate()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const rows = told.data ?? []

  return (
    <Panel title={eventId ? 'How it is told' : 'What they call them'}
           count={rows.length || undefined}>
      {error && <ErrorBox error={error} />}

      {rows.length === 0 && !adding && (
        <p className="muted small">
          Nobody has a version of {subject} on record yet. Who tells it differently?
        </p>
      )}

      <div className="accounts">
      {rows.map((row) => (
        <div key={row.id} className="account">
          <div className="account-who">
            {row.holder_name || <em className="muted">nobody in particular</em>}
          </div>
          <div>
            <div className="account-label">“{row.label}”</div>
            {row.account && <div className="small muted">{row.account}</div>}
          </div>
          <button className="danger small" onClick={() => void drop(row.id)}>
            remove
          </button>
        </div>
      ))}
      </div>

      {adding ? (
        <div className="form-stack" style={{ marginTop: 8 }}>
          <EntityPicker label="Whose version? (leave empty for one nobody owns)"
                        chosen={holder} onChoose={setHolder} />
          <label className="field">
            <div className="small muted">
              {eventId ? 'What they call it' : 'What they call them'}
            </div>
            <input autoFocus value={label}
                   placeholder={eventId ? 'The northern account' : 'The Pretender'}
                   onChange={(e) => setLabel(e.target.value)} />
          </label>
          <label className="field">
            <div className="small muted">And what they say (optional)</div>
            <input value={account}
                   placeholder="A massacre of men who had already asked for terms."
                   onChange={(e) => setAccount(e.target.value)} />
          </label>
          <div className="form-actions">
            <button onClick={() => { setAdding(false); setError(null) }}>Cancel</button>
            <button className="active" disabled={!label.trim()}
                    onClick={() => void keep()}>Record it</button>
          </div>
        </div>
      ) : (
        <button style={{ marginTop: 8 }} onClick={() => setAdding(true)}>
          + another version
        </button>
      )}
    </Panel>
  )
}
