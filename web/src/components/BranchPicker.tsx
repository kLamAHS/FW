/**
 * Timelines (§105). A branch is an overlay, never a copy: it inherits the whole world
 * and keeps its own changes to itself. Nothing done on a branch can touch the main
 * timeline, which is what makes "what if?" safe to actually try.
 *
 * Like the world picker, switching or forking ends in a full reload — every cached
 * view belongs to the timeline it was fetched from.
 */

import { useState } from 'react'
import { api } from '../api'
import { ErrorBox, Loading, useAsync } from './common'

export function BranchPicker({ day, dateText }: { day: number; dateText: string }) {
  const branches = useAsync(() => api.branches(), [])
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const act = async (work: () => Promise<unknown>) => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await work()
      window.location.reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  if (branches.loading) return <Loading what="Reading the timelines" />
  if (branches.error) return <ErrorBox error={branches.error} />
  const list = branches.data ?? []

  return (
    <div className="form-stack">
      <div className="field">
        <div className="small muted">Timelines of this world</div>
        <div className="picker-results" style={{ position: 'static', maxHeight: '38vh' }}>
          {list.map((b) => (
            <button key={b.id} className="entity-line"
                    disabled={busy || b.open}
                    onClick={() => void act(() => api.openBranch(b.name))}>
              <span className="name">{b.name}</span>
              <span className="type-chip">
                {b.open ? 'you are here' : b.is_canon ? 'main timeline' : 'what-if'}
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <div className="small muted">Fork a new timeline</div>
        <input value={name} placeholder="What if…?"
               aria-label="New timeline name"
               onChange={(e) => setName(e.target.value)} />
      </div>
      <p className="muted small">
        The new timeline splits from this one on {dateText || 'the current date'}. It
        inherits everything; everything you change in it stays in it. The main
        timeline cannot be altered from a branch — only added to its side of history.
      </p>

      {error && <div className="error-box small">{error}</div>}

      <div className="form-actions">
        <button className="active" disabled={!name.trim() || busy}
                onClick={() => void act(() => api.createBranch(name.trim(), day))}>
          {busy ? 'Working…' : 'Fork the timeline'}
        </button>
      </div>
    </div>
  )
}
