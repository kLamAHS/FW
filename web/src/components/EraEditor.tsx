/**
 * The world's own time dividers (§3) — its equivalent of BC and AD.
 *
 * An era renames years for reading and writing; it never touches a stored fact, which is
 * why one can be declared long after the history is written and nothing moves. The
 * distinction the form has to teach, in as few words as possible, is the one that makes
 * BC work: some ages count *forward* from their founding, and some count *backward*
 * toward it, so 100 BR is earlier than 50 BR.
 */

import { useState } from 'react'
import { api } from '../api'
import type { EraRow } from '../api'
import { ErrorBox, Loading, useAsync } from './common'

interface Draft {
  name: string
  abbreviation: string
  start_year: string
  end_year: string
  counts_backward: boolean
  reckons_from: string
}

const EMPTY: Draft = {
  name: '', abbreviation: '', start_year: '', end_year: '',
  counts_backward: false, reckons_from: '',
}

const num = (text: string): number | null =>
  text.trim() === '' ? null : Number(text)

export function EraEditor({ onChanged }: { onChanged: () => void }) {
  const [generation, setGeneration] = useState(0)
  const eras = useAsync(() => api.eras(), [generation])
  const [draft, setDraft] = useState<Draft>(EMPTY)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = () => {
    setGeneration((g) => g + 1)
    onChanged()
  }

  const act = async (work: () => Promise<unknown>) => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await work()
      refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const create = () =>
    act(async () => {
      await api.createEra({
        name: draft.name.trim(),
        abbreviation: draft.abbreviation.trim(),
        start_year: num(draft.start_year),
        end_year: num(draft.end_year),
        counts_backward: draft.counts_backward,
        reckons_from: num(draft.reckons_from),
      })
      setDraft(EMPTY)
    })

  if (eras.loading && !eras.data) return <Loading what="Reading the eras" />
  if (eras.error && !eras.data) return <ErrorBox error={eras.error} />

  return (
    <div className="form-stack">
      {(eras.data ?? []).length > 0 && (
        <div className="field">
          <div className="small muted">This world's ages</div>
          <ul className="clean small">
            {(eras.data ?? []).map((e) => (
              <li key={e.id} style={{ display: 'flex', gap: 8, alignItems: 'baseline',
                                      padding: '3px 0' }}>
                <strong>{e.abbreviation}</strong>
                <span>{e.name}</span>
                <span className="muted">{describeEra(e)}</span>
                <span className="spacer" style={{ flex: 1 }} />
                <button className="small danger"
                        title="Remove this divider — no dated fact changes"
                        onClick={() => void act(() => api.deleteEra(e.id))}>
                  ✕
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {(eras.data ?? []).length === 0 && (
        <p className="muted small">
          No ages yet. Years are shown as plain numbers until you name a divider.
        </p>
      )}

      <div className="field">
        <div className="small muted">Add an age</div>
        <div style={{ display: 'flex', gap: 6 }}>
          <input value={draft.name} placeholder="Age of the Reckoning"
                 aria-label="Era name" style={{ flex: 2 }}
                 onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          <input value={draft.abbreviation} placeholder="AR"
                 aria-label="Era short form" style={{ flex: 1, minWidth: 70 }}
                 onChange={(e) => setDraft({ ...draft, abbreviation: e.target.value })} />
        </div>
      </div>

      <div className="field">
        <div className="small muted">
          Which years it covers — leave a side blank for an age with no end
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="number" value={draft.start_year} placeholder="from year"
                 aria-label="First year" style={{ width: 110 }}
                 onChange={(e) => setDraft({ ...draft, start_year: e.target.value })} />
          <span className="muted small">to</span>
          <input type="number" value={draft.end_year} placeholder="to year"
                 aria-label="Last year" style={{ width: 110 }}
                 onChange={(e) => setDraft({ ...draft, end_year: e.target.value })} />
        </div>
      </div>

      <label className="small" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <input type="checkbox" checked={draft.counts_backward}
               onChange={(e) => setDraft({ ...draft, counts_backward: e.target.checked })} />
        Counts backwards, the way BC does — earlier years get bigger numbers
      </label>

      <div className="field">
        <div className="small muted">
          {draft.counts_backward
            ? 'Counts back from this year (blank: from just after the age ends)'
            : 'Renumbers from this year (blank: years keep their plain numbers)'}
        </div>
        <input type="number" value={draft.reckons_from} placeholder="year one"
               aria-label="Reckons from" style={{ width: 130 }}
               onChange={(e) => setDraft({ ...draft, reckons_from: e.target.value })} />
      </div>

      <p className="muted small">
        A world's BC and AD is two ages: one ending at year 0 that counts backwards, and
        one starting at year 1. Between them there is no year zero — year 0 <em>is</em> the
        first year of the backward age.
      </p>

      {error && <div className="error-box small">{error}</div>}

      <div className="form-actions">
        <button className="active"
                disabled={!draft.name.trim() || !draft.abbreviation.trim() || busy}
                onClick={() => void create()}>
          Add this age
        </button>
      </div>
    </div>
  )
}

function describeEra(e: EraRow): string {
  const from = e.start_year === null ? 'the beginning' : `year ${e.start_year}`
  const to = e.end_year === null ? 'onward' : `year ${e.end_year}`
  const span = e.end_year === null ? `${from} onward` : `${from} to ${to}`
  if (e.counts_backward) return `${span}, counting backwards`
  if (e.reckons_from !== null) return `${span}, renumbered from ${e.reckons_from}`
  return span
}
