/**
 * The global timeline slider (§3, §36, §80).
 *
 * The brief's signature interaction: choose a year and have maps, titles, political
 * control, borders, alliances, marriages, living characters and wars all move with it. The
 * slider owns the date for the whole application, and every view reads it.
 *
 * The date is spelled out in words beside the slider rather than left implicit in the
 * handle position. §70 is explicit that the interface must not assume the reader can
 * picture where they are — "somewhere near the right-hand end" is not a date.
 */

import { useState } from 'react'
import type { WorldSummary } from '../api'

interface Props {
  world: WorldSummary
  day: number
  onChange: (day: number) => void
  snapshots: { id: string; name: string; day: number }[]
  dateText: string
  season: string | null
  onEditEras: () => void
  /** §80: name this moment, or take a name back off. */
  onName: (name: string) => void
  onForget: (id: string) => void
}

export function Timeline(
  { world, day, onChange, snapshots, dateText, season, onEditEras,
    onName, onForget }: Props,
) {
  const [naming, setNaming] = useState<string | null>(null)
  const { first, last } = world.span
  const year = Math.floor((day - first) / world.calendar.days_in_year)

  const step = (years: number) => onChange(
    Math.min(last, Math.max(first, day + years * world.calendar.days_in_year)),
  )

  return (
    <div className="timeline">
      <label htmlFor="timeline-slider" className="small muted" style={{ whiteSpace: 'nowrap' }}>
        World date
      </label>

      <button onClick={() => step(-10)} title="Back ten years" aria-label="Back ten years">
        ⏮
      </button>
      <button onClick={() => step(-1)} title="Back one year" aria-label="Back one year">
        ◀
      </button>

      <input
        id="timeline-slider"
        type="range"
        min={first}
        max={last}
        value={day}
        step={1}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-valuetext={dateText}
      />

      <button onClick={() => step(1)} title="Forward one year" aria-label="Forward one year">
        ▶
      </button>
      <button onClick={() => step(10)} title="Forward ten years" aria-label="Forward ten years">
        ⏭
      </button>

      <span className="date" title="The date the whole application is showing">
        {dateText}
      </span>
      {season && <span className="season">{season}</span>}
      <span className="muted small mono" title="Year within the world's recorded span">
        yr {year}
      </span>
      {/* §3: how years are *named* belongs beside where the date is read. */}
      <button className="small" onClick={onEditEras}
              title="Name the ages of this world — its own BC and AD">
        ages
      </button>

      {/* §80. The chips have been here since the timeline was written and only the
          seeded world could have any: "Before the Red War" is how a writer holds a
          date, and 81400 is not. */}
      <span className="snapshots">
        {snapshots.map((s) => (
          <span key={s.id} className="snapshot-chip">
            <button
              onClick={() => onChange(s.day)}
              className={s.day === day ? 'active' : ''}
              title={`Jump to ${s.name}`}
            >
              {s.name}
            </button>
            <button className="forget" title={`Stop calling that day “${s.name}”`}
                    onClick={() => onForget(s.id)}>×</button>
          </span>
        ))}
        {naming === null ? (
          <button className="name-this" title="Give this date a name you can come back to"
                  onClick={() => setNaming('')}>
            + name this date
          </button>
        ) : (
          <>
            <input autoFocus value={naming} placeholder="Before the Red War"
                   onChange={(e) => setNaming(e.target.value)}
                   onKeyDown={(e) => {
                     if (e.key === 'Enter' && naming.trim()) {
                       onName(naming.trim())
                       setNaming(null)
                     }
                     if (e.key === 'Escape') setNaming(null)
                   }} />
            <button className="active" disabled={!naming.trim()}
                    onClick={() => { onName(naming.trim()); setNaming(null) }}>
              Name it
            </button>
            <button onClick={() => setNaming(null)}>Cancel</button>
          </>
        )}
      </span>
    </div>
  )
}
