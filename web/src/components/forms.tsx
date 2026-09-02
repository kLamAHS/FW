/**
 * Creating and editing (§56, §60).
 *
 * §56 is the governing constraint: "The UI must not expose hundreds of fields
 * immediately… Avoid intimidating forms." A new entity asks for three things — what it
 * is, what it is called, a sentence about it — and everything else lives behind one
 * disclosure. A writer who wants to add "a village called Thornby" should be done in
 * five seconds.
 *
 * Dates are entered as in-world dates (year, month by name, day) and resolved to day
 * indices by the server, because the calendar's leap rules live there and a second
 * client-side implementation would eventually disagree with the first.
 */

import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api'
import type {
  CalendarInfo, Chapter, Entity, EntityDraft, EventDraft, SceneDraft, Source,
  Vocabulary,
} from '../api'
import { useAsync, useDebounced } from './common'

/* ------------------------------------------------------------------ modal */

// Mirrors `fw.core.model.vocabulary`. The server validates against the same lists and
// says so in words, so a mismatch here is a nuisance rather than a corruption.
const SECRET_SEVERITIES = ['trivial', 'minor', 'major', 'catastrophic'] as const
const KNOWLEDGE_STANCES =
  ['knows', 'believes', 'suspects', 'misinformed', 'unaware'] as const

export function Modal(
  { title, onClose, children }:
  { title: string; onClose: () => void; children: ReactNode },
) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-overlay" onClick={onClose} role="presentation">
      <div className="modal-card" role="dialog" aria-label={title}
           onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>
  )
}

/* ------------------------------------------------------- in-world date input */

export interface CivilDraft {
  year: string
  month: number
  day: string
  /** Abbreviation of the age the year is said in; '' means a plain absolute year. */
  era?: string
}

export const EMPTY_DATE: CivilDraft = { year: '', month: 1, day: '1', era: '' }

export function WorldDateInput(
  { label, calendar, value, onChange, hint }:
  {
    label: string
    calendar: CalendarInfo
    value: CivilDraft
    onChange: (next: CivilDraft) => void
    hint?: string
  },
) {
  const monthDays = calendar.months[value.month - 1]?.days ?? 31
  return (
    <label className="field">
      <div className="small muted">{label}{hint && <span> — {hint}</span>}</div>
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          type="number"
          placeholder="year"
          value={value.year}
          onChange={(e) => onChange({ ...value, year: e.target.value })}
          style={{ width: 90 }}
          aria-label={`${label}: year`}
        />
        <select
          value={value.month}
          onChange={(e) => onChange({ ...value, month: Number(e.target.value) })}
          disabled={value.year === ''}
          aria-label={`${label}: month`}
        >
          {calendar.months.map((m, i) => (
            <option key={m.name} value={i + 1}>{m.name}</option>
          ))}
        </select>
        <input
          type="number"
          min={1}
          max={monthDays + 1}     /* +1 leaves room for a leap day */
          value={value.day}
          onChange={(e) => onChange({ ...value, day: e.target.value })}
          disabled={value.year === ''}
          style={{ width: 64 }}
          aria-label={`${label}: day`}
        />
        {/* §3: a world that names its ages must let a date be *typed* in them too —
            otherwise "100 BR" can be read but never entered, and a backward age is
            unusable. Hidden entirely when the world has no ages. */}
        {calendar.eras.length > 0 && (
          <select
            value={value.era ?? ''}
            onChange={(e) => onChange({ ...value, era: e.target.value })}
            disabled={value.year === ''}
            aria-label={`${label}: age`}
            title="Which age the year is counted in"
          >
            <option value="">plain year</option>
            {calendar.eras.map((e) => (
              <option key={e.abbreviation} value={e.abbreviation}>{e.abbreviation}</option>
            ))}
          </select>
        )}
      </div>
    </label>
  )
}

/** Resolve a drafted date to a day index, or null when the year was left blank. */
export async function resolveDate(draft: CivilDraft): Promise<number | null> {
  if (draft.year.trim() === '') return null
  const result = await api.dayIndex(
    Number(draft.year), draft.month, Number(draft.day) || 1, draft.era || null)
  return result.day
}

/** The inverse: a stored day index as a draft the form can show, in era terms. */
export async function draftFromDay(day: number): Promise<CivilDraft> {
  const date = await api.date(day)
  return {
    year: String(date.era && date.era_year !== null ? date.era_year : date.year),
    month: date.month,
    day: String(date.day_of_month),
    era: date.era ?? '',
  }
}

/* ------------------------------------------------------------- entity form */

const CONFIDENCE_LEVELS = ['canon', 'draft', 'tentative', 'rumored', 'disputed',
                           'speculative'] as const

export function EntityForm(
  { vocabulary, calendar, existing, initialType, onDone, onCancel }:
  {
    vocabulary: Vocabulary
    calendar: CalendarInfo
    existing?: Entity
    initialType?: string
    onDone: (entity: Entity) => void
    onCancel: () => void
  },
) {
  const [typeKey, setTypeKey] = useState(existing?.type_key ?? initialType ?? 'person')
  const [name, setName] = useState(existing?.name ?? '')
  const [summary, setSummary] = useState(existing?.summary ?? '')
  const [more, setMore] = useState(false)
  const [from, setFrom] = useState<CivilDraft>(EMPTY_DATE)
  const [to, setTo] = useState<CivilDraft>(EMPTY_DATE)
  const [confidence, setConfidence] = useState(existing?.confidence ?? 'canon')

  // When editing, show the dates the entity already has. Blank-on-save now genuinely
  // clears a date (that is the fix the form's hint promises), so starting the fields
  // empty over real dates would turn "open More detail, save" into silent erasure.
  useEffect(() => {
    let cancelled = false
    const load = async (day: number | null, set: (d: CivilDraft) => void) => {
      if (day === null) return
      // Show the date the way the world says it — in its age, if it has one — so
      // editing a date entered as "100 BR" does not silently redisplay it as -99.
      const draft = await draftFromDay(day)
      if (!cancelled) set(draft)
    }
    if (existing) {
      void load(existing.exists_from, setFrom)
      void load(existing.exists_to, setTo)
    }
    return () => { cancelled = true }
  }, [existing])
  const [tags, setTags] = useState(existing?.tags.join(', ') ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const byCategory = new Map<string, typeof vocabulary.entity_types>()
  for (const t of vocabulary.entity_types) {
    byCategory.set(t.category, [...(byCategory.get(t.category) ?? []), t])
  }

  const chosen = vocabulary.entity_types.find((t) => t.key === typeKey)
  const isPerson = typeKey === 'person'
  const fromLabel = isPerson ? 'Born' : 'Founded or created'
  const toLabel = isPerson ? 'Died' : 'Destroyed or ended'

  const submit = async () => {
    if (busy) return                       // Enter twice must not create twice
    if (!name.trim()) { setError('It needs a name.'); return }
    setBusy(true)
    setError(null)
    try {
      const draft: EntityDraft = {
        type_key: typeKey,
        name: name.trim(),
        summary: summary.trim(),
        confidence,
        tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
      }
      // An edit that never opened "more detail" must not blank existing dates.
      if (more || !existing) {
        draft.exists_from = await resolveDate(from)
        draft.exists_to = await resolveDate(to)
      }
      const entity = existing
        ? await api.updateEntity(existing.id, draft)
        : await api.createEntity(draft)
      onDone(entity)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="form-stack">
      {!existing && (
        <label className="field">
          <div className="small muted">What is it?</div>
          <select value={typeKey} onChange={(e) => setTypeKey(e.target.value)}
                  autoFocus={!existing}>
            {[...byCategory.entries()].map(([category, types]) => (
              <optgroup key={category} label={category}>
                {types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
              </optgroup>
            ))}
          </select>
        </label>
      )}

      <label className="field">
        <div className="small muted">Name</div>
        <input value={name} onChange={(e) => setName(e.target.value)}
               placeholder={chosen ? `A ${chosen.label.toLowerCase()}…` : 'A name…'}
               autoFocus={Boolean(existing)}
               onKeyDown={(e) => { if (e.key === 'Enter') void submit() }} />
      </label>

      <label className="field">
        <div className="small muted">A sentence about it (optional)</div>
        <input value={summary} onChange={(e) => setSummary(e.target.value)}
               placeholder="What should you remember about it?" />
      </label>

      {/* §56: everything beyond the essentials is opt-in. */}
      <button className="disclosure" onClick={() => setMore((v) => !v)}
              aria-expanded={more}>
        {more ? '▾' : '▸'} More detail — dates, certainty, tags
      </button>

      {more && (
        <>
          <WorldDateInput label={fromLabel} calendar={calendar} value={from}
                          onChange={setFrom} hint="leave the year blank for “always”" />
          <WorldDateInput label={toLabel} calendar={calendar} value={to}
                          onChange={setTo} />
          <label className="field">
            <div className="small muted">How certain is this? (§57)</div>
            <select value={confidence} onChange={(e) => setConfidence(e.target.value)}>
              {CONFIDENCE_LEVELS.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label className="field">
            <div className="small muted">Tags, separated by commas</div>
            <input value={tags} onChange={(e) => setTags(e.target.value)}
                   placeholder="northern, ally, chapter-3" />
          </label>
        </>
      )}

      {error && <div className="error-box">{error}</div>}

      <div className="form-actions">
        <button onClick={onCancel}>Cancel</button>
        <button className="active" onClick={() => void submit()} disabled={busy}>
          {busy ? 'Saving…' : existing ? 'Save changes' : `Add to the world`}
        </button>
      </div>
    </div>
  )
}

/* --------------------------------------------------------------- fact form */

const SECRECY_LEVELS = ['public', 'known', 'discreet', 'secret', 'deep_secret'] as const


export function FactForm(
  { subject, vocabulary, calendar, onDone, onCancel }:
  {
    subject: Entity
    vocabulary: Vocabulary
    calendar: CalendarInfo
    onDone: () => void
    onCancel: () => void
  },
) {
  const relationCategories = new Map<string, typeof vocabulary.predicates>()
  const propertyCategories = new Map<string, typeof vocabulary.predicates>()
  for (const p of vocabulary.predicates) {
    const bucket = p.kind === 'rel' ? relationCategories : propertyCategories
    bucket.set(p.category, [...(bucket.get(p.category) ?? []), p])
  }

  const [predicateKey, setPredicateKey] = useState('trusts')
  const [target, setTarget] = useState<Entity | null>(null)
  const [value, setValue] = useState('')
  const [strength, setStrength] = useState('')
  const [secrecy, setSecrecy] = useState('public')
  const [from, setFrom] = useState<CivilDraft>(EMPTY_DATE)
  const [note, setNote] = useState('')
  const [source, setSource] = useState('')
  const [sources, setSources] = useState<Source[]>([])
  const [newSource, setNewSource] = useState<{ label: string; kind: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // §58: the citation was rendered on every fact line before anything could make one.
  useEffect(() => {
    void api.sources().then(setSources).catch(() => setSources([]))
  }, [])

  const predicate = vocabulary.predicates.find((p) => p.key === predicateKey)
  const isRelationship = predicate?.kind === 'rel'

  const scaleSteps: { value: string; label: string }[] = (() => {
    if (!predicate?.scale_key) return []
    const scale = vocabulary.scales.find((s) => s.key === predicate.scale_key)
    if (!scale) return []
    try {
      return JSON.parse(scale.steps) as { value: string; label: string }[]
    } catch {
      return []
    }
  })()

  const keepSource = async () => {
    if (!newSource?.label.trim()) return
    try {
      const made = await api.addSource(newSource.label.trim(), newSource.kind)
      setSources(await api.sources())
      setSource(made.id)
      setNewSource(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const submit = async () => {
    if (busy) return
    setError(null)
    if (isRelationship && !target) { setError('Pick who or what it connects to.'); return }
    if (!isRelationship && !value.trim()) { setError('It needs a value.'); return }
    setBusy(true)
    try {
      await api.createFact({
        subject_id: subject.id,
        predicate_key: predicateKey,
        object_id: isRelationship ? target!.id : null,
        value: isRelationship ? null : value.trim(),
        strength: strength || null,
        secrecy,
        valid_from: await resolveDate(from),
        note: note.trim(),
        source_id: source || null,
      })
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="form-stack">
      <label className="field">
        <div className="small muted">{subject.name}…</div>
        <select value={predicateKey} autoFocus
                onChange={(e) => { setPredicateKey(e.target.value); setStrength('') }}>
          {[...relationCategories.entries()].map(([category, predicates]) => (
            <optgroup key={category} label={category}>
              {predicates.map((p) => (
                <option key={p.key} value={p.key}>{p.label}</option>
              ))}
            </optgroup>
          ))}
          {[...propertyCategories.entries()].map(([category, predicates]) => (
            <optgroup key={`prop-${category}`} label={`${category} (a property)`}>
              {predicates.map((p) => (
                <option key={p.key} value={p.key}>{p.label}</option>
              ))}
            </optgroup>
          ))}
        </select>
        {predicate?.description && (
          <div className="small muted" style={{ marginTop: 3 }}>{predicate.description}</div>
        )}
      </label>

      {isRelationship ? (
        <EntityPicker label="…whom or what?" chosen={target} onChoose={setTarget}
                      excludeId={subject.id} />
      ) : (
        <label className="field">
          <div className="small muted">Value</div>
          <input value={value} onChange={(e) => setValue(e.target.value)} />
        </label>
      )}

      {scaleSteps.length > 0 && (
        <label className="field">
          <div className="small muted">How strongly? (§5 — your own words, not numbers)</div>
          <select value={strength} onChange={(e) => setStrength(e.target.value)}>
            <option value="">unspecified</option>
            {scaleSteps.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </label>
      )}

      <label className="field">
        <div className="small muted">Who knows about this? (§6)</div>
        <select value={secrecy} onChange={(e) => setSecrecy(e.target.value)}>
          {SECRECY_LEVELS.map((s) => (
            <option key={s} value={s}>{s.replace('_', ' ')}</option>
          ))}
        </select>
      </label>

      <WorldDateInput label="True from" calendar={calendar} value={from}
                      onChange={setFrom} hint="blank means it has always been so" />

      <label className="field">
        <div className="small muted">A note (optional)</div>
        <input value={note} onChange={(e) => setNote(e.target.value)}
               placeholder="Why, or how it came to be" />
      </label>

      {/* §58. A picker with nothing in it is the half-finished shape this whole
          commit exists to remove, so a source can be made from right here. */}
      <label className="field">
        <div className="small muted">Where this came from (§58)</div>
        {newSource ? (
          <div className="toolbar">
            <input autoFocus value={newSource.label} placeholder="Chapter 3 draft"
                   onChange={(e) => setNewSource({ ...newSource, label: e.target.value })} />
            <select value={newSource.kind}
                    onChange={(e) => setNewSource({ ...newSource, kind: e.target.value })}>
              {vocabulary.source_kinds.map((k) => (
                <option key={k.key} value={k.key}>{k.label}</option>
              ))}
            </select>
            <button className="active" disabled={!newSource.label.trim()}
                    onClick={() => void keepSource()}>Add it</button>
            <button onClick={() => setNewSource(null)}>Cancel</button>
          </div>
        ) : (
          <select value={source}
                  onChange={(e) => {
                    if (e.target.value === '+') {
                      setNewSource({ label: '', kind: 'author_note' })
                    } else {
                      setSource(e.target.value)
                    }
                  }}>
            <option value="">not cited</option>
            {sources.map((s) => (
              <option key={s.id} value={s.id}>{s.label} — {s.label_kind}</option>
            ))}
            <option value="+">a source you have not recorded yet…</option>
          </select>
        )}
      </label>

      {error && <div className="error-box">{error}</div>}

      <div className="form-actions">
        <button onClick={onCancel}>Cancel</button>
        <button className="active" onClick={() => void submit()} disabled={busy}>
          {busy ? 'Saving…' : 'Record it'}
        </button>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------ entity picker */

export function EntityPicker(
  { label, chosen, onChoose, excludeId, typeKey }:
  {
    label: string
    chosen: Entity | null
    onChoose: (entity: Entity | null) => void
    excludeId?: string
    typeKey?: string
  },
) {
  const [text, setText] = useState('')
  const query = useDebounced(text)
  // useAsync already discards out-of-order responses. The hand-rolled version this
  // replaces reset its own cancellation flag on the next run, so a slow response for
  // "Nor" could land under an input reading "Northmarch" — and the writer would record
  // the relationship against whatever stale row they clicked.
  const found = useAsync(
    () => (query.trim() ? api.search(query, typeKey) : Promise.resolve([])),
    [query, typeKey],
  )
  const results = (found.data ?? [])
    .filter((e) => e.id !== excludeId)
    .slice(0, 8)

  if (chosen) {
    return (
      <div className="field">
        <div className="small muted">{label}</div>
        <div className="picked">
          <span className="name">{chosen.name}</span>
          <span className="type-chip">{chosen.type_key.replace(/_/g, ' ')}</span>
          <button onClick={() => onChoose(null)} aria-label="Choose someone else">✕</button>
        </div>
      </div>
    )
  }

  return (
    <div className="field">
      <div className="small muted">{label}</div>
      <input value={text} onChange={(e) => setText(e.target.value)}
             placeholder="Type a name…" aria-label={label} />
      {results.length > 0 && (
        <div className="picker-results">
          {results.map((e) => (
            // Clear the query on choose: without this, un-choosing (or adding the next
            // participant through MultiEntityPicker) resurfaces the previous search's
            // results under an input the writer believes is empty.
            <button key={e.id} className="entity-line"
                    onClick={() => { setText(''); onChoose(e) }}>
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


/* --------------------------------------------------------- multi-entity picker */

export function MultiEntityPicker(
  { label, chosen, onChange, typeKey }:
  {
    label: string
    chosen: Entity[]
    onChange: (next: Entity[]) => void
    typeKey?: string
  },
) {
  return (
    <div className="field">
      <div className="small muted">{label}</div>
      {chosen.length > 0 && (
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', margin: '4px 0' }}>
          {chosen.map((e) => (
            <span key={e.id} className="picked" style={{ padding: '2px 7px', marginTop: 0 }}>
              <span className="name">{e.name}</span>
              <button onClick={() => onChange(chosen.filter((c) => c.id !== e.id))}
                      aria-label={`Remove ${e.name}`}>✕</button>
            </span>
          ))}
        </div>
      )}
      <EntityPicker
        label=""
        chosen={null}
        typeKey={typeKey}
        onChoose={(e) => {
          if (e && !chosen.some((c) => c.id === e.id)) onChange([...chosen, e])
        }}
      />
    </div>
  )
}

/* --------------------------------------------------------------- scene form */

export function SceneForm(
  { calendar, onDone, onCancel }:
  { calendar: CalendarInfo; onDone: () => void; onCancel: () => void },
) {
  const [title, setTitle] = useState('')
  const [when, setWhen] = useState<CivilDraft>(EMPTY_DATE)
  const [location, setLocation] = useState<Entity | null>(null)
  const [pov, setPov] = useState<Entity | null>(null)
  const [participants, setParticipants] = useState<Entity[]>([])
  const [objective, setObjective] = useState('')
  const [conflict, setConflict] = useState('')
  const [chapter, setChapter] = useState('')
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // The book, if there is one. A world with no manuscript in it yet simply does not
  // see the field, rather than seeing an empty dropdown it cannot fill.
  useEffect(() => {
    void api.chapters().then(setChapters).catch(() => setChapters([]))
  }, [])

  const submit = async () => {
    if (busy) return
    if (!title.trim()) { setError('It needs a title.'); return }
    setBusy(true)
    setError(null)
    try {
      const draft: SceneDraft = {
        title: title.trim(),
        chapter_id: chapter || null,
        day: await resolveDate(when),
        location_id: location?.id ?? null,
        pov_id: pov?.id ?? null,
        objective: objective.trim(),
        conflict: conflict.trim(),
        // The POV character is in the room too — do not make the writer add them twice.
        participants: [...new Set([
          ...participants.map((p) => p.id),
          ...(pov ? [pov.id] : []),
        ])],
      }
      await api.createScene(draft)
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="form-stack">
      <label className="field">
        <div className="small muted">Title</div>
        <input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus
               placeholder="The Winter Feast at…" />
      </label>
      <WorldDateInput label="When" calendar={calendar} value={when} onChange={setWhen}
                      hint="the context panel is built from this date" />
      {chapters.length > 0 && (
        <label className="field">
          <div className="small muted">Where it sits in the book</div>
          <select value={chapter} onChange={(e) => setChapter(e.target.value)}>
            <option value="">not placed yet</option>
            {chapters.map((c) => (
              <option key={c.id} value={c.id}>{c.work_title} — {c.title}</option>
            ))}
          </select>
        </label>
      )}
      <EntityPicker label="Where" chosen={location} onChoose={setLocation} />
      <EntityPicker label="Whose eyes (POV)" chosen={pov} onChoose={setPov}
                    typeKey="person" />
      <MultiEntityPicker label="Who else is present" chosen={participants}
                         onChange={setParticipants} typeKey="person" />
      <label className="field">
        <div className="small muted">Objective — what someone wants here</div>
        <input value={objective} onChange={(e) => setObjective(e.target.value)} />
      </label>
      <label className="field">
        <div className="small muted">Conflict — what stands in the way</div>
        <input value={conflict} onChange={(e) => setConflict(e.target.value)} />
      </label>
      {error && <div className="error-box">{error}</div>}
      <div className="form-actions">
        <button onClick={onCancel}>Cancel</button>
        <button className="active" onClick={() => void submit()} disabled={busy}>
          {busy ? 'Saving…' : 'Create the scene'}
        </button>
      </div>
    </div>
  )
}

/* --------------------------------------------------------------- event form */

const EVENT_KINDS = ['event', 'battle', 'war', 'treaty', 'rebellion', 'coronation',
                     'marriage', 'famine', 'plague', 'discovery', 'assassination',
                     'natural disaster'] as const

const PARTICIPANT_ROLES = ['participant', 'commander', 'witness', 'victim',
                           'belligerent', 'signatory', 'author'] as const

export function EventForm(
  { calendar, onDone, onCancel }:
  { calendar: CalendarInfo; onDone: () => void; onCancel: () => void },
) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<string>('event')
  const [summary, setSummary] = useState('')
  const [when, setWhen] = useState<CivilDraft>(EMPTY_DATE)
  const [location, setLocation] = useState<Entity | null>(null)
  const [participants, setParticipants] = useState<Entity[]>([])
  const [roles, setRoles] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (busy) return
    if (!name.trim()) { setError('It needs a name.'); return }
    setBusy(true)
    setError(null)
    try {
      const draft: EventDraft = {
        name: name.trim(),
        type_key: kind,
        summary: summary.trim(),
        start_day: await resolveDate(when),
        location_id: location?.id ?? null,
        participants: participants.map((p) => ({
          id: p.id, role: roles[p.id] ?? 'participant',
        })),
      }
      await api.createEvent(draft)
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="form-stack">
      <label className="field">
        <div className="small muted">What happened?</div>
        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus
               placeholder="The Battle of…" />
      </label>
      <label className="field">
        <div className="small muted">Kind</div>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {EVENT_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
      </label>
      <WorldDateInput label="When" calendar={calendar} value={when} onChange={setWhen}
                      hint="leave blank if nobody recorded the date" />
      <EntityPicker label="Where" chosen={location} onChoose={setLocation} />
      <MultiEntityPicker label="Who was involved" chosen={participants}
                         onChange={setParticipants} />
      {participants.length > 0 && (
        <div className="field">
          <div className="small muted">Their parts in it</div>
          {participants.map((p) => (
            <div key={p.id} style={{ display: 'flex', gap: 8, alignItems: 'center',
                                     padding: '2px 0' }}>
              <span style={{ flex: 1 }}>{p.name}</span>
              <select value={roles[p.id] ?? 'participant'}
                      onChange={(e) => setRoles({ ...roles, [p.id]: e.target.value })}>
                {PARTICIPANT_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
          ))}
        </div>
      )}
      <label className="field">
        <div className="small muted">What should be remembered about it</div>
        <input value={summary} onChange={(e) => setSummary(e.target.value)} />
      </label>
      {error && <div className="error-box">{error}</div>}
      <div className="form-actions">
        <button onClick={onCancel}>Cancel</button>
        <button className="active" onClick={() => void submit()} disabled={busy}>
          {busy ? 'Saving…' : 'Record the event'}
        </button>
      </div>
    </div>
  )
}

/**
 * A title, so there is something for anyone to inherit (§8).
 *
 * `World.add_title` has existed since the world model did — revision-logged,
 * branch-scoped, with a succession engine already able to run a line over it — and had
 * no route and no form. So succession worked on the seeded example world and on nothing
 * a writer built for themselves, and the Succession screen was, for them, an empty page
 * with no way to fill it.
 */
export function TitleForm(
  { calendar, laws, onDone, onCancel }:
  {
    calendar: CalendarInfo
    laws: { key: string; label: string; description: string }[]
    onDone: () => void
    onCancel: () => void
  },
) {
  const [name, setName] = useState('')
  const [rank, setRank] = useState(1)
  const [law, setLaw] = useState('male_preference_primogeniture')
  const [territory, setTerritory] = useState<Entity | null>(null)
  const [holder, setHolder] = useState<Entity | null>(null)
  const [since, setSince] = useState<CivilDraft>(EMPTY_DATE)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (busy) return
    if (!name.trim()) { setError('A title needs a name.'); return }
    setBusy(true)
    setError(null)
    try {
      const made = await api.createTitle({
        name: name.trim(), rank, succession_law: law,
        territory_id: territory?.id ?? null,
      })
      // The first holder in the same breath: a title nobody holds is a title the
      // succession engine has nothing to run a line from.
      if (holder) {
        await api.grantTitle(made.id, {
          holder_id: holder.id, from_day: await resolveDate(since), how: 'inheritance',
        })
      }
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const chosen = laws.find((l) => l.key === law)
  return (
    <div className="form-stack">
      <label className="field">
        <div className="small muted">What is it called?</div>
        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus
               placeholder="Warden of the Northmarch" />
      </label>
      <label className="field">
        <div className="small muted">How high it stands — a king outranks a warden</div>
        <input type="number" value={rank} min={0} max={10}
               onChange={(e) => setRank(Number(e.target.value) || 0)} />
      </label>
      <label className="field">
        <div className="small muted">How it passes on</div>
        <select value={law} onChange={(e) => setLaw(e.target.value)}>
          {laws.map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
        </select>
        {chosen && <div className="small muted">{chosen.description}</div>}
      </label>
      <EntityPicker label="Over what ground" chosen={territory} onChoose={setTerritory} />
      <EntityPicker label="Who holds it now" chosen={holder} onChoose={setHolder} />
      {holder && (
        <WorldDateInput label="Since" calendar={calendar} value={since} onChange={setSince}
                        hint="leave blank if it has always been theirs" />
      )}
      {error && <div className="error-box">{error}</div>}
      <div className="form-actions">
        <button onClick={onCancel}>Cancel</button>
        <button className="active" onClick={() => void submit()} disabled={busy}>
          {busy ? 'Saving…' : 'Create the title'}
        </button>
      </div>
    </div>
  )
}

/**
 * A secret, and who thinks what about it (§6).
 *
 * The truth lives in one place; what each person believes lives beside it as a stance.
 * That separation is the brief's own: "who knows X" and "who believes X" have to be
 * separately answerable, and a boolean on the fact cannot do it. Nor can it hold the
 * second-order case — Edric believes that *Mara* believes the wrong thing — which is
 * what `about` is for, and what a scene usually turns on.
 */
export function SecretForm(
  { calendar, onDone, onCancel }:
  { calendar: CalendarInfo; onDone: () => void; onCancel: () => void },
) {
  const [name, setName] = useState('')
  const [truth, setTruth] = useState('')
  const [severity, setSeverity] = useState('major')
  const [about, setAbout] = useState<Entity | null>(null)
  const [observer, setObserver] = useState<Entity | null>(null)
  const [stance, setStance] = useState('knows')
  const [wrongAbout, setWrongAbout] = useState<Entity | null>(null)
  const [since, setSince] = useState<CivilDraft>(EMPTY_DATE)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (busy) return
    if (!name.trim()) { setError('A secret needs a name to file it under.'); return }
    setBusy(true)
    setError(null)
    try {
      const made = await api.createSecret({
        name: name.trim(), truth: truth.trim(), severity,
        about_id: about?.id ?? null,
      })
      if (observer) {
        await api.recordKnowledge({
          observer_id: observer.id, secret_id: made.id, stance,
          about_observer_id: wrongAbout?.id ?? null,
          acquired_on: await resolveDate(since),
        })
      }
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="form-stack">
      <label className="field">
        <div className="small muted">What is the secret called?</div>
        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus
               placeholder="The forged will" />
      </label>
      <label className="field">
        <div className="small muted">What is actually true</div>
        <input value={truth} onChange={(e) => setTruth(e.target.value)}
               placeholder="It was written a week after he died." />
      </label>
      <label className="field">
        <div className="small muted">What it costs if it comes out</div>
        <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
          {SECRET_SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>
      <EntityPicker label="Who or what it is about" chosen={about} onChoose={setAbout} />
      <hr />
      <p className="small muted">
        And somebody's position on it. You can add more from the secret afterwards.
      </p>
      <EntityPicker label="Who" chosen={observer} onChoose={setObserver} />
      {observer && (
        <>
          <label className="field">
            <div className="small muted">What they think</div>
            <select value={stance} onChange={(e) => setStance(e.target.value)}>
              {KNOWLEDGE_STANCES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <EntityPicker label="…about whose position (leave empty for the plain case)"
                        chosen={wrongAbout} onChoose={setWrongAbout} />
          <WorldDateInput label="Since when" calendar={calendar} value={since}
                          onChange={setSince}
                          hint="leave blank if they have always known" />
        </>
      )}
      {error && <div className="error-box">{error}</div>}
      <div className="form-actions">
        <button onClick={onCancel}>Cancel</button>
        <button className="active" onClick={() => void submit()} disabled={busy}>
          {busy ? 'Saving…' : 'Record the secret'}
        </button>
      </div>
    </div>
  )
}
