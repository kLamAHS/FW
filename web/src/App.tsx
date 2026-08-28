/**
 * The application shell.
 *
 * Three pieces of global state, all deliberate.
 *
 * **The date** (§3) belongs to the whole application, not to the map. Moving the slider
 * has to move every view at once, or the writer ends up holding "which view is showing
 * which year" in their head — the exact working-memory load §70 exists to remove.
 *
 * **The selection** (§76) opens the side panel rather than navigating, so inspecting a
 * house from the map does not lose the map.
 *
 * **The mutation version** exists because editing happens in the side panel while a view
 * stays on screen. Every successful edit bumps it, every view depends on it, so a rename
 * in the panel repaints the map behind it — the two must never disagree about the world.
 */

import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import { ErrorBox, Loading, useAsync, useDebounced } from './components/common'
import { EntityForm, Modal } from './components/forms'
import { BranchPicker } from './components/BranchPicker'
import { EraEditor } from './components/EraEditor'
import { Launcher, WorldPicker } from './components/WorldPicker'
import { SidePanel } from './components/SidePanel'
import { Timeline } from './components/Timeline'
import { Dashboard } from './views/Dashboard'
import { MapView } from './views/MapView'
import { GraphView } from './views/GraphView'
import { PedigreeView } from './views/PedigreeView'
import { SceneView } from './views/SceneView'
import { SuccessionView } from './views/SuccessionView'
import { TravelView } from './views/TravelView'
import { ContinuityView, EntitiesView, EventsView } from './views/BrowseView'
import { GroupsView } from './views/GroupsView'

const VIEWS = [
  { key: 'dashboard', label: 'World' },
  { key: 'map', label: 'Map' },
  { key: 'timeline', label: 'History' },
  { key: 'pedigree', label: 'Family' },
  { key: 'graph', label: 'Relationships' },
  { key: 'succession', label: 'Succession' },
  { key: 'scenes', label: 'Scenes' },
  { key: 'travel', label: 'Travel' },
  { key: 'groups', label: 'Groups' },
  { key: 'entities', label: 'Everything' },
  { key: 'continuity', label: 'Checks' },
] as const

export function App() {
  const [version, setVersion] = useState(0)
  const bump = () => setVersion((v) => v + 1)

  const world = useAsync(() => api.world(), [version])
  // Gated on the world being open: on the launcher these would only 409.
  const vocabulary = useAsync(
    () => (world.data ? api.vocabulary() : Promise.resolve(null)),
    [world.data !== null],
  )
  const snapshots = useAsync(
    () => (world.data ? api.snapshots() : Promise.resolve(null)),
    [world.data !== null],
  )
  const [view, setView] = useState<string>('dashboard')
  const [day, setDay] = useState<number | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [picking, setPicking] = useState(false)
  const [branching, setBranching] = useState(false)
  const [editingEras, setEditingEras] = useState(false)

  const currentDay = day ?? world.data?.present_day ?? 0
  const date = useAsync(
    () => (world.data ? api.date(currentDay) : Promise.resolve(null)),
    [currentDay, world.data !== null],
  )

  // Undo/redo (§59). The buttons show what they would take back; Ctrl+Z / Ctrl+Y work
  // anywhere the writer is not typing into a field.
  const undoState = useAsync(
    () => (world.data ? api.undoState() : Promise.resolve(null)),
    [version, world.data !== null],
  )
  const [toast, setToast] = useState<string | null>(null)
  const toastTimer = useRef<number | undefined>(undefined)
  const announce = (text: string) => {
    setToast(text)
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(null), 4000)
  }
  const timeTravel = useRef<(direction: 'undo' | 'redo') => void>(() => {})
  timeTravel.current = async (direction) => {
    try {
      const result = direction === 'undo' ? await api.undo() : await api.redo()
      announce(result.message)
      bump()
    } catch (err) {
      announce(err instanceof Error ? err.message : String(err))
    }
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA'
                     || target.tagName === 'SELECT' || target.isContentEditable)) {
        return    // never steal the shortcut from a text field's own undo
      }
      if (!(e.ctrlKey || e.metaKey)) return
      const key = e.key.toLowerCase()
      if (key === 'z' && !e.shiftKey) {
        e.preventDefault()
        timeTravel.current('undo')
      } else if (key === 'y' || (key === 'z' && e.shiftKey)) {
        e.preventDefault()
        timeTravel.current('redo')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (world.loading && !world.data) return <Loading what="Opening the world" />
  // No world open (409) means the launcher, not an error: the writer picks a save or
  // names a new world — nobody is forced into a template.
  if (!world.data && world.error instanceof ApiError && world.error.status === 409) {
    return <Launcher />
  }
  // Fatal only when there is nothing to show. /api/world now refetches after every
  // edit, and one failed refetch must not tear down the writer's whole session —
  // stale-but-present data keeps the app alive until the next successful load.
  if (world.error && !world.data) return <ErrorBox error={world.error} />
  if (!world.data) return null

  const dateText = date.data?.text ?? ''

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>{world.data.name}</h1>
          <div className="world-sub">
            {world.data.counts.total} entities · {world.data.calendar.name} calendar
          </div>
        </div>
        <nav className="nav" aria-label="Views">
          {VIEWS.map((v) => (
            <button key={v.key} className={view === v.key ? 'active' : ''}
                    aria-current={view === v.key ? 'page' : undefined}
                    onClick={() => setView(v.key)}>
              {v.label}
            </button>
          ))}
        </nav>
        <span style={{ flex: 1 }} />
        <button onClick={() => timeTravel.current('undo')}
                disabled={!undoState.data?.can_undo}
                aria-label="Undo"
                title={undoState.data?.undo
                  ? `Undo ${undoState.data.undo} (Ctrl+Z)` : 'Nothing to undo'}>
          ↶
        </button>
        <button onClick={() => timeTravel.current('redo')}
                disabled={!undoState.data?.can_redo}
                aria-label="Redo"
                title={undoState.data?.redo
                  ? `Redo ${undoState.data.redo} (Ctrl+Y)` : 'Nothing to redo'}>
          ↷
        </button>
        <button onClick={() => setBranching(true)}
                className={world.data.branch.is_canon ? '' : 'active'}
                title="Alternate timelines: fork a what-if, or switch between them">
          ⑂ {world.data.branch.is_canon ? 'Timelines' : world.data.branch.name}
        </button>
        <button onClick={() => setPicking(true)}
                title="Open another world, or start a new one">
          Worlds
        </button>
        <button className="active" onClick={() => setCreating(true)}
                title="Add something to the world">
          + New
        </button>
        <SearchBox onSelect={(id) => setSelected(id)} version={version} />
      </header>

      {!world.data.branch.is_canon && (
        <div className="branch-banner" role="status">
          You are on the <strong>{world.data.branch.name}</strong> timeline — nothing
          here touches the main one.
          <button className="small"
                  onClick={() => void api.openBranch('canon')
                    .then(() => window.location.reload())}>
            Return to the main timeline
          </button>
        </div>
      )}

      <Timeline
        world={world.data}
        day={currentDay}
        onChange={setDay}
        snapshots={(snapshots.data ?? []).map((s) => ({ name: s.name, day: s.day }))}
        dateText={dateText}
        season={date.data?.season ?? null}
        onEditEras={() => setEditingEras(true)}
      />

      <div className="main">
        <main className="content">
          {view === 'dashboard' && (
            <Dashboard world={world.data} day={currentDay} dateText={dateText}
                       onSelect={setSelected} onGo={setView} version={version}
                       onMutate={bump} />
          )}
          {view === 'map' && (
            <MapView day={currentDay} onSelect={setSelected} selectedId={selected}
                     version={version} />
          )}
          {view === 'timeline' && (
            <EventsView day={currentDay} onSelect={setSelected} version={version}
                        calendar={world.data.calendar} onMutate={bump} />
          )}
          {view === 'pedigree' && (
            <PedigreeView day={currentDay} onSelect={setSelected} selectedId={selected}
                          version={version} />
          )}
          {view === 'graph' && (
            <GraphView day={currentDay} onSelect={setSelected} selectedId={selected}
                       version={version} />
          )}
          {view === 'succession' && (
            <SuccessionView day={currentDay} onSelect={setSelected} version={version}
                            vocabulary={vocabulary.data} />
          )}
          {view === 'scenes' && (
            <SceneView onSelect={setSelected} version={version}
                       calendar={world.data.calendar} onMutate={bump} />
          )}
          {view === 'travel' && <TravelView day={currentDay} dateText={dateText} />}
          {view === 'groups' && (
            <GroupsView day={currentDay} onSelect={setSelected} version={version} />
          )}
          {view === 'entities' && (
            <EntitiesView world={world.data} day={currentDay} onSelect={setSelected}
                          version={version} vocabulary={vocabulary.data} />
          )}
          {view === 'continuity' && (
            <ContinuityView onSelect={setSelected} version={version} />
          )}
        </main>

        {selected && (
          <SidePanel
            entityId={selected}
            day={currentDay}
            dateText={dateText}
            vocabulary={vocabulary.data}
            calendar={world.data.calendar}
            onClose={() => setSelected(null)}
            onSelect={setSelected}
            onMutate={bump}
            version={version}
          />
        )}
      </div>

      {toast && <div className="toast" role="status">{toast}</div>}

      {picking && (
        <Modal title="Your worlds" onClose={() => setPicking(false)}>
          <WorldPicker />
        </Modal>
      )}

      {branching && (
        <Modal title="Alternate timelines" onClose={() => setBranching(false)}>
          <BranchPicker day={currentDay} dateText={dateText} />
        </Modal>
      )}

      {editingEras && (
        <Modal title="The ages of this world" onClose={() => setEditingEras(false)}>
          {/* Naming an age changes how every date reads, so the whole app refetches. */}
          <EraEditor onChanged={bump} />
        </Modal>
      )}

      {creating && (
        <Modal title="Add to the world" onClose={() => setCreating(false)}>
          {vocabulary.data ? (
            <EntityForm
              vocabulary={vocabulary.data}
              calendar={world.data.calendar}
              onDone={(entity) => {
                setCreating(false)
                bump()
                setSelected(entity.id)   // open what was just made, ready to connect
              }}
              onCancel={() => setCreating(false)}
            />
          ) : (
            // A failed vocabulary load must not leave "+ New" silently dead: say what
            // went wrong, in the modal the click opened, with a way to retry.
            <div className="form-stack">
              {vocabulary.loading
                ? <Loading what="Loading the world's vocabulary" />
                : (
                  <>
                    <div className="error-box">
                      The world's vocabulary did not load, so nothing can be created yet.
                    </div>
                    <div className="form-actions">
                      <button onClick={() => setCreating(false)}>Close</button>
                      <button className="active" onClick={vocabulary.reload}>
                        Try again
                      </button>
                    </div>
                  </>
                )}
            </div>
          )}
        </Modal>
      )}
    </div>
  )
}

/** Universal search (§53). Debounced, and it opens the side panel rather than navigating. */
function SearchBox(
  { onSelect, version }: { onSelect: (id: string) => void; version: number },
) {
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const query = useDebounced(text)
  const { data } = useAsync(
    () => (query.trim() ? api.search(query) : Promise.resolve([])),
    [query, version],
  )

  return (
    <div className="search-box">
      <input
        type="search"
        placeholder="Search the world…"
        value={text}
        onChange={(e) => { setText(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 160)}
        aria-label="Search the world"
      />
      {open && query.trim() && (
        <div className="search-results">
          {(data ?? []).length === 0 && <div className="empty small">Nothing found</div>}
          {(data ?? []).map((e) => (
            <button key={e.id} className="entity-line"
                    onMouseDown={() => { onSelect(e.id); setText(''); setOpen(false) }}>
              <span className="name">{e.name}</span>
              <span className="type-chip">{e.type_key.replace(/_/g, ' ')}</span>
              <span className="desc">{e.summary}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
