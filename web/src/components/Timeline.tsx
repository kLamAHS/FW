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

import type { WorldSummary } from '../api'

interface Props {
  world: WorldSummary
  day: number
  onChange: (day: number) => void
  snapshots: { name: string; day: number }[]
  dateText: string
  season: string | null
}

export function Timeline({ world, day, onChange, snapshots, dateText, season }: Props) {
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

      {snapshots.length > 0 && (
        <span className="snapshots">
          {snapshots.map((s) => (
            <button
              key={s.name}
              onClick={() => onChange(s.day)}
              className={s.day === day ? 'active' : ''}
              title={`Jump to ${s.name}`}
            >
              {s.name}
            </button>
          ))}
        </span>
      )}
    </div>
  )
}
