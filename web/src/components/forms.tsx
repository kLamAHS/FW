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

import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api'
import type { CalendarInfo, Entity, EntityDraft, Vocabulary } from '../api'
import { useDebounced } from './common'

/* ------------------------------------------------------------------ modal */

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
}

export const EMPTY_DATE: CivilDraft = { year: '', month: 1, day: '1' }

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
      </div>
    </label>
  )
}

/** Resolve a drafted date to a day index, or null when the year was left blank. */
export async function resolveDate(draft: CivilDraft): Promise<number | null> {
  if (draft.year.trim() === '') return null
  const result = await api.dayIndex(
    Number(draft.year), draft.month, Number(draft.day) || 1)
  return result.day
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
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

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

  const submit = async () => {
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

function EntityPicker(
  { label, chosen, onChoose, excludeId }:
  {
    label: string
    chosen: Entity | null
    onChoose: (entity: Entity | null) => void
    excludeId?: string
  },
) {
  const [text, setText] = useState('')
  const query = useDebounced(text)
  const [results, setResults] = useState<Entity[]>([])
  const cancelled = useRef(false)

  useEffect(() => {
    cancelled.current = false
    if (!query.trim()) { setResults([]); return }
    api.search(query)
      .then((found) => {
        if (!cancelled.current) {
          setResults(found.filter((e) => e.id !== excludeId).slice(0, 8))
        }
      })
      .catch(() => setResults([]))
    return () => { cancelled.current = true }
  }, [query, excludeId])

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
            <button key={e.id} className="entity-line" onClick={() => onChoose(e)}>
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
