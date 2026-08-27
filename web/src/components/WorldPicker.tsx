/**
 * The launcher: the writer's saves.
 *
 * A world is one portable file, so this screen is a directory listing with manners —
 * open a save, or start a new world. The example kingdom is offered, never imposed:
 * nobody building their own world should have to dig out of a template first.
 *
 * Used two ways: full-screen when the server has no world open yet, and inside a modal
 * (the "Worlds" button) for switching. Opening or creating always ends in a full page
 * reload — the calendar, vocabulary and every cached view belong to the old world, and
 * a clean boot is the one transition guaranteed to leave nothing of it behind.
 */

import { useState } from 'react'
import { api } from '../api'
import { ErrorBox, Loading, useAsync } from './common'

export function WorldPicker() {
  const library = useAsync(() => api.worlds(), [])
  const [name, setName] = useState('')
  const [example, setExample] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)   // file being opened, or 'new'
  const [error, setError] = useState<string | null>(null)

  const finish = () => {
    // A different world invalidates everything the client holds; reboot clean.
    window.location.reload()
  }

  const open = async (file: string) => {
    setBusy(file)
    setError(null)
    try {
      await api.openWorld(file)
      finish()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }

  const create = async () => {
    if (!name.trim()) return
    setBusy('new')
    setError(null)
    try {
      await api.createWorld(name.trim(), example)
      finish()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }

  if (library.loading) return <Loading what="Reading your library" />
  if (library.error) return <ErrorBox error={library.error} />
  const info = library.data
  if (!info) return null

  if (info.library === null) {
    return (
      <p className="muted">
        This server was started on a single world file, so there is no library to
        switch within — restart it with plain <code>fw serve</code> to use saves.
      </p>
    )
  }

  return (
    <div className="form-stack">
      {info.worlds.length > 0 && (
        <div className="field">
          <div className="small muted">Your worlds</div>
          <div className="picker-results" style={{ position: 'static', maxHeight: '40vh' }}>
            {info.worlds.map((w) => (
              <button key={w.file} className="entity-line"
                      disabled={busy !== null || w.problem !== '' || w.file === info.open}
                      onClick={() => void open(w.file)}>
                <span className="name">{w.name}</span>
                <span className="type-chip">
                  {w.file === info.open ? 'open now'
                    : w.problem ? 'unreadable'
                    : `${w.entities} ${w.entities === 1 ? 'entity' : 'entities'}`}
                </span>
                <span className="desc">
                  {w.problem || `${w.file} · ${new Date(w.modified * 1000).toLocaleString()}`}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
      {info.worlds.length === 0 && (
        <p className="muted">
          No worlds yet. Name one below and start building — everything you make is
          saved automatically into one file in <code>{info.library}</code>.
        </p>
      )}

      <div className="field">
        <div className="small muted">Start a new world</div>
        <input value={name} placeholder="What is this world called?"
               aria-label="New world name"
               onChange={(e) => setName(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') void create() }} />
      </div>
      <label className="small" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <input type="checkbox" checked={example}
               onChange={(e) => setExample(e.target.checked)} />
        Start with the example Kingdom of Renn — a small finished world that shows
        what everything here can do
      </label>

      {error && <div className="error-box small">{error}</div>}

      <div className="form-actions">
        <button className="active" disabled={!name.trim() || busy !== null}
                onClick={() => void create()}>
          {busy === 'new' ? 'Creating…' : 'Create this world'}
        </button>
      </div>
    </div>
  )
}

/** The full-screen version, shown when the server has no world open at all. */
export function Launcher() {
  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center',
                  padding: 24 }}>
      <div className="panel" style={{ width: 'min(560px, 92vw)' }}>
        <h1 className="serif" style={{ marginBottom: 4 }}>FW</h1>
        <p className="muted" style={{ marginBottom: 14 }}>
          A place to keep a fictional world outside your head.
        </p>
        <WorldPicker />
      </div>
    </div>
  )
}
