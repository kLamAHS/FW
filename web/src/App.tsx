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

import { useState } from 'react'
import { api } from './api'
import { ErrorBox, Loading, useAsync, useDebounced } from './components/common'
import { EntityForm, Modal } from './components/forms'
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

const VIEWS = [
  { key: 'dashboard', label: 'World' },
  { key: 'map', label: 'Map' },
  { key: 'timeline', label: 'History' },
  { key: 'pedigree', label: 'Family' },
  { key: 'graph', label: 'Relationships' },
  { key: 'succession', label: 'Succession' },
  { key: 'scenes', label: 'Scenes' },
  { key: 'travel', label: 'Travel' },
  { key: 'entities', label: 'Everything' },
  { key: 'continuity', label: 'Checks' },
] as const

export function App() {
  const [version, setVersion] = useState(0)
  const bump = () => setVersion((v) => v + 1)

  const world = useAsync(() => api.world(), [version])
  const vocabulary = useAsync(() => api.vocabulary(), [])
  const snapshots = useAsync(() => api.snapshots(), [])
  const [view, setView] = useState<string>('dashboard')
  const [day, setDay] = useState<number | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const currentDay = day ?? world.data?.present_day ?? 0
  const date = useAsync(
    () => (world.data ? api.date(currentDay) : Promise.resolve(null)),
    [currentDay, world.data !== null],
  )

  if (world.loading && !world.data) return <Loading what="Opening the world" />
  if (world.error) return <ErrorBox error={world.error} />
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
        <button className="active" onClick={() => setCreating(true)}
                title="Add something to the world">
          + New
        </button>
        <SearchBox onSelect={(id) => setSelected(id)} version={version} />
      </header>

      <Timeline
        world={world.data}
        day={currentDay}
        onChange={setDay}
        snapshots={(snapshots.data ?? []).map((s) => ({ name: s.name, day: s.day }))}
        dateText={dateText}
        season={date.data?.season ?? null}
      />

      <div className="main">
        <main className="content">
          {view === 'dashboard' && (
            <Dashboard world={world.data} day={currentDay} dateText={dateText}
                       onSelect={setSelected} onGo={setView} version={version} />
          )}
          {view === 'map' && (
            <MapView day={currentDay} onSelect={setSelected} selectedId={selected}
                     version={version} />
          )}
          {view === 'timeline' && (
            <EventsView day={currentDay} onSelect={setSelected} version={version} />
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
            <SuccessionView day={currentDay} onSelect={setSelected} version={version} />
          )}
          {view === 'scenes' && <SceneView onSelect={setSelected} version={version} />}
          {view === 'travel' && <TravelView day={currentDay} dateText={dateText} />}
          {view === 'entities' && (
            <EntitiesView world={world.data} day={currentDay} onSelect={setSelected}
                          version={version} />
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
          />
        )}
      </div>

      {creating && vocabulary.data && (
        <Modal title="Add to the world" onClose={() => setCreating(false)}>
          <EntityForm
            vocabulary={vocabulary.data}
            calendar={world.data.calendar}
            onDone={(entity) => {
              setCreating(false)
              bump()
              setSelected(entity.id)     // open what was just made, ready to connect
            }}
            onCancel={() => setCreating(false)}
          />
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
