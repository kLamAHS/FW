/**
 * Putting a question to the world (§49).
 *
 * The brief calls this one of the application's most important features, and until now
 * the only questions a writer could ask were the ones somebody had built a screen for.
 *
 * A question here is built rather than typed. That is the whole design: a text query
 * language means a grammar to document and syntax errors written for somebody who came
 * here to write a novel, whereas a form can only produce questions the engine can
 * answer, and every one it can answer is one the form can offer. The shape it builds is
 * exactly `fw.core.query.Query`, so what the screen sends is what a saved question
 * stores and what the API takes.
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type {
  Entity, QueryAnswer, QueryCondition, QueryShape, QueryVocabulary, SavedQuery,
  Vocabulary,
} from '../api'
import { EntityPicker } from '../components/forms'
import { Badge, ErrorBox, Loading, Panel, useAsync } from '../components/common'

const BLANK: QueryShape = { types: [], conditions: [], order: 'name', limit: 100 }

// The questions the brief itself asks, offered as a starting point. Not because a
// writer cannot build them — they can, in three clicks — but because a form with no
// example in it is a form nobody knows what to do with.
const EXAMPLES: { label: string; build: (world: Entity[]) => QueryShape }[] = [
  {
    label: 'Which houses answer to nobody?',
    build: () => ({
      ...BLANK, types: ['house'],
      conditions: [{ predicate: 'vassal_of', negate: true }],
    }),
  },
  {
    label: 'Every port',
    build: () => ({
      ...BLANK, types: ['settlement'],
      conditions: [{ predicate: 'settlement_type', test: 'is', value: 'port' }],
    }),
  },
  {
    label: 'Towns of more than 20,000',
    build: () => ({
      ...BLANK, types: ['settlement'],
      conditions: [{ predicate: 'population', test: 'greater_than', value: '20000' }],
    }),
  },
  {
    label: 'Anything you marked uncertain',
    build: () => ({ ...BLANK, confidence: ['rumored', 'disputed', 'tentative'] }),
  },
]

interface Props {
  day: number
  vocabulary: Vocabulary | null
  onSelect: (id: string) => void
  version: number
}

export function AskView({ day, vocabulary, onSelect, version }: Props) {
  const [query, setQuery] = useState<QueryShape>(BLANK)
  const [answer, setAnswer] = useState<QueryAnswer | null>(null)
  const [asking, setAsking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showWorking, setShowWorking] = useState(false)
  const [saveAs, setSaveAs] = useState('')
  const [kept, setKept] = useState<SavedQuery[]>([])
  // The picker works in whole entities and the query stores an id, so the chosen one is
  // held beside the condition rather than looked up again on every keystroke.
  const [chosen, setChosen] = useState<Record<number, Entity | null>>({})
  const words = useAsync<QueryVocabulary>(() => api.queryVocabulary(), [version])

  useEffect(() => {
    void api.savedQueries().then(setKept).catch(() => setKept([]))
  }, [version])

  const ask = async (shape: QueryShape = query) => {
    setAsking(true)
    setError(null)
    try {
      setAnswer(await api.ask({ ...shape, explain: true }))
    } catch (err) {
      setAnswer(null)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setAsking(false)
    }
  }

  const keep = async () => {
    if (!saveAs.trim()) return
    try {
      await api.saveQuery(saveAs.trim(), query)
      setSaveAs('')
      setKept(await api.savedQueries())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const drop = async (key: string) => {
    await api.forgetQuery(key)
    setKept(await api.savedQueries())
  }

  const set = (patch: Partial<QueryShape>) => setQuery((q) => ({ ...q, ...patch }))
  const setCondition = (n: number, patch: Partial<QueryCondition>) =>
    set({ conditions: (query.conditions ?? []).map((c, i) =>
      i === n ? { ...c, ...patch } : c) })

  const types = vocabulary?.entity_types ?? []
  const predicates = vocabulary?.predicates ?? []

  return (
    <>
      <Panel title="Ask the world a question">
        <p className="muted small">
          Every question is built from what your world actually records, so there is no
          syntax to get wrong and nothing here can ask something the answer cannot mean.
        </p>

        <div className="toolbar" style={{ flexWrap: 'wrap' }}>
          {EXAMPLES.map((example) => (
            <button key={example.label}
                    onClick={() => { const q = example.build([]); setQuery(q); void ask(q) }}>
              {example.label}
            </button>
          ))}
        </div>

        <div className="ask-row">
          <label className="small">Kind of thing</label>
          <select multiple size={4} value={query.types ?? []}
                  onChange={(e) => set({
                    types: [...e.target.selectedOptions].map((o) => o.value),
                  })}>
            {types.map((t) => (
              <option key={t.key} value={t.key}>{t.plural}</option>
            ))}
          </select>

          <label className="small">Name contains</label>
          <input value={query.name_contains ?? ''}
                 placeholder="ford"
                 onChange={(e) => set({ name_contains: e.target.value })} />

          <label className="small">How sure you were</label>
          <select multiple size={4} value={query.confidence ?? []}
                  onChange={(e) => set({
                    confidence: [...e.target.selectedOptions].map((o) => o.value),
                  })}>
            {(words.data?.confidence ?? []).map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>

          <label className="small">Existing on</label>
          <label className="small" style={{ display: 'flex', gap: 6 }}>
            <input type="checkbox" checked={query.exists_on != null}
                   onChange={(e) => set({ exists_on: e.target.checked ? day : null })} />
            the date on the timeline
          </label>
        </div>

        {/* Conditions over the fact spine. Each one narrows: an answer has to satisfy
            all of them, which is the shape a form can offer honestly. */}
        {(query.conditions ?? []).map((condition, n) => (
          <div className="ask-condition" key={n}>
            <select value={condition.direction ?? 'out'}
                    onChange={(e) => setCondition(n, {
                      direction: e.target.value as 'out' | 'in',
                    })}>
              <option value="out">it …</option>
              <option value="in">something … it</option>
            </select>
            <select value={condition.predicate}
                    onChange={(e) => setCondition(n, { predicate: e.target.value })}>
              <option value="">choose a relationship…</option>
              {predicates.map((p) => (
                <option key={p.key} value={p.key}>{p.label}</option>
              ))}
            </select>
            <select value={condition.test ?? 'exists'}
                    onChange={(e) => setCondition(n, { test: e.target.value })}>
              {(words.data?.tests ?? []).map((t) => (
                <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>
              ))}
            </select>
            {!['exists', 'missing'].includes(condition.test ?? 'exists') && (
              <input value={condition.value ?? ''} placeholder="value"
                     onChange={(e) => setCondition(n, { value: e.target.value })} />
            )}
            <EntityPicker label="the other end"
                          chosen={chosen[n] ?? null}
                          onChoose={(entity) => {
                            setChosen((prev) => ({ ...prev, [n]: entity }))
                            setCondition(n, { object_id: entity?.id ?? '' })
                          }} />
            <label className="small" style={{ display: 'flex', gap: 5 }}>
              <input type="checkbox" checked={condition.negate ?? false}
                     onChange={(e) => setCondition(n, { negate: e.target.checked })} />
              not
            </label>
            <button className="danger"
                    onClick={() => set({
                      conditions: (query.conditions ?? []).filter((_, i) => i !== n),
                    })}>
              remove
            </button>
          </div>
        ))}

        <div className="toolbar">
          <button onClick={() => set({
            conditions: [...(query.conditions ?? []), { predicate: '' }],
          })}>
            + a condition
          </button>
          <button className="active" disabled={asking} onClick={() => void ask()}>
            {asking ? 'Asking…' : 'Ask'}
          </button>
          <button onClick={() => { setQuery(BLANK); setAnswer(null) }}>Start over</button>
          <span className="spacer" style={{ flex: 1 }} />
          <input value={saveAs} placeholder="keep this question as…"
                 onChange={(e) => setSaveAs(e.target.value)} />
          <button disabled={!saveAs.trim()} onClick={() => void keep()}>Keep it</button>
        </div>
      </Panel>

      {error && <ErrorBox error={error} />}
      {asking && !answer && <Loading what="Asking" />}

      {answer && (
        <Panel title="The answer" count={answer.total}>
          {answer.notes.map((note) => (
            <p key={note} className="muted small">{note}</p>
          ))}
          {answer.rows.length === 0 && (
            <p className="muted">Nothing in your world matches that.</p>
          )}
          {answer.rows.map((row) => (
            <button key={row.id} className="entity-line" onClick={() => onSelect(row.id)}>
              <span className="name">{row.name}</span>
              <Badge>{row.type_key}</Badge>
              {row.distance != null && <Badge>{row.distance} away</Badge>}
              {row.confidence !== 'canon' && (
                <Badge kind="disputed">{row.confidence}</Badge>
              )}
              <span className="desc">
                {row.because.length ? row.because.join('; ') : row.summary}
              </span>
            </button>
          ))}
          {answer.truncated && (
            <p className="muted small">
              Showing {answer.rows.length} of {answer.total}. Narrow the question, or
              raise the limit.
            </p>
          )}
          <p className="muted small" style={{ marginTop: 8 }}>
            {answer.total} in {answer.ms} ms.{' '}
            <button style={{ border: 0, background: 'none', padding: 0 }}
                    onClick={() => setShowWorking((v) => !v)}>
              {showWorking ? 'hide the working' : 'show the working'}
            </button>
          </p>
          {showWorking && <pre className="small mono">{answer.sql}</pre>}
        </Panel>
      )}

      {kept.length > 0 && (
        <Panel title="Questions you keep asking" count={kept.length}>
          {kept.map((row) => (
            <div key={row.key} className="entity-line">
              <button className="name" style={{ border: 0, background: 'none' }}
                      onClick={() => { setQuery(row.query); void ask(row.query) }}>
                {row.name}
              </button>
              <span className="desc">{row.note}</span>
              <button className="danger" onClick={() => void drop(row.key)}>forget</button>
            </div>
          ))}
        </Panel>
      )}
    </>
  )
}
