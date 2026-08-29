/**
 * Succession, including the hypothetical sandbox (§8, §50).
 *
 * §50 requires that "changes in hypothetical mode must never alter canonical data", so the
 * hypothesis controls are query parameters, not edits — nothing here can write to the
 * world. The canonical line stays on screen beside the hypothetical one, because the
 * question a writer is actually asking is *what would change*, and answering it with a
 * single list they have to remember the previous version of would defeat the purpose.
 */

import { useState } from 'react'
import { api } from '../api'
import type { CalendarInfo, Vocabulary } from '../api'
import { Badge, ErrorBox, Loading, Panel, useAsync } from '../components/common'
import { Modal, TitleForm } from '../components/forms'

interface Props {
  day: number
  onSelect: (id: string) => void
  version: number
  vocabulary: Vocabulary | null
  calendar: CalendarInfo
  onMutate: () => void
}

export function SuccessionView(
  { day, onSelect, version, vocabulary, calendar, onMutate }: Props,
) {
  const titles = useAsync(() => api.titles(day), [day, version])
  const [making, setMaking] = useState(false)
  const [titleId, setTitleId] = useState<string | null>(null)
  const [law, setLaw] = useState<string>('')
  const [illegitimate, setIllegitimate] = useState<string>('')
  const [dead, setDead] = useState<string>('')

  const chosen = titleId ?? titles.data?.[0]?.id ?? null

  const canonical = useAsync(
    () => (chosen ? api.succession(chosen, { day }) : Promise.resolve(null)),
    [chosen, day, version],
  )
  const hypothetical = useAsync(
    () => (chosen && (law || illegitimate || dead)
      ? api.succession(chosen, {
          day,
          law_key: law || undefined,
          illegitimate: illegitimate || undefined,
          assume_dead: dead || undefined,
        })
      : Promise.resolve(null)),
    [chosen, day, law, illegitimate, dead, version],
  )

  const people = (canonical.data?.line ?? []).concat(
    (canonical.data?.excluded ?? []).map((e) => ({
      position: 0, id: e.id, name: e.name, note: '',
    })),
  )

  // Making a title is what turns this page from a demonstration into a tool: the
  // succession engine has always worked and there was no way to give it anything to
  // work on, so a writer's own world showed them an empty screen.
  const make = (
    <>
      <button className="active" onClick={() => setMaking(true)}>+ A title</button>
      {making && (
        <Modal title="A title" onClose={() => setMaking(false)}>
          <TitleForm calendar={calendar} laws={vocabulary?.succession_laws ?? []}
                     onDone={() => { setMaking(false); onMutate() }}
                     onCancel={() => setMaking(false)} />
        </Modal>
      )}
    </>
  )

  if (titles.loading) return <Loading />
  if (titles.error) return <ErrorBox error={titles.error} />
  if (!titles.data?.length) {
    return (
      <Panel title="Succession">
        <p className="muted">
          Nothing in this world is inherited yet. A title is the thing a line of
          succession runs down — make one and its heirs are worked out from your own
          family tree and the law you choose.
        </p>
        <div className="toolbar">{make}</div>
      </Panel>
    )
  }

  const anyHypothesis = Boolean(law || illegitimate || dead)

  return (
    <>
      <div className="toolbar">
        <span className="small muted">Title</span>
        {titles.data.map((t) => (
          <button key={t.id} className={t.id === chosen ? 'active' : ''}
                  onClick={() => setTitleId(t.id)}>
            {t.name}
          </button>
        ))}
        <span className="spacer" style={{ flex: 1 }} />
        {make}
      </div>

      <div className="grid wide">
        <Panel title="As things stand">
          {canonical.loading && <Loading />}
          {canonical.error ? <ErrorBox error={canonical.error} /> : null}
          {canonical.data && (
            <SuccessionLine result={canonical.data} onSelect={onSelect} />
          )}
        </Panel>

        <Panel title="What if…">
          <p className="muted small">
            Nothing here changes your world. These are questions, not edits.
          </p>

          <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
            <label>
              <div className="small muted">Under a different law</div>
              <select value={law} onChange={(e) => setLaw(e.target.value)}
                      style={{ width: '100%' }}>
                <option value="">
                  {canonical.data ? `${canonical.data.law_label} (as written)` : 'as written'}
                </option>
                {(vocabulary?.succession_laws ?? []).map((l) => (
                  <option key={l.key} value={l.key}>{l.label}</option>
                ))}
              </select>
            </label>

            <label>
              <div className="small muted">If this person were declared illegitimate</div>
              <select value={illegitimate} onChange={(e) => setIllegitimate(e.target.value)}
                      style={{ width: '100%' }}>
                <option value="">nobody</option>
                {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>

            <label>
              <div className="small muted">If this person were dead</div>
              <select value={dead} onChange={(e) => setDead(e.target.value)}
                      style={{ width: '100%' }}>
                <option value="">nobody</option>
                {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>

            {anyHypothesis && (
              <button onClick={() => { setLaw(''); setIllegitimate(''); setDead('') }}>
                Clear the hypothesis
              </button>
            )}
          </div>

          {hypothetical.loading && <Loading what="Recomputing" />}
          {hypothetical.data && (
            <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
              <div style={{ marginBottom: 8 }}>
                <Badge kind="warning">△ hypothetical</Badge>{' '}
                <span className="small muted">
                  {hypothetical.data.assumptions.join('; ') || hypothetical.data.law_label}
                </span>
              </div>
              <SuccessionLine result={hypothetical.data} onSelect={onSelect}
                              compareWith={canonical.data?.line.map((c) => c.id)} />
            </div>
          )}
        </Panel>
      </div>

      {canonical.data && canonical.data.excluded.length > 0 && (
        <Panel title="Who is not in the line, and why">
          <ul className="clean">
            {canonical.data.excluded.map((e) => (
              <li key={e.id}>
                <button className="entity-line" style={{ width: 'auto', padding: 0 }}
                        onClick={() => onSelect(e.id)}>
                  <span className="name">{e.name}</span>
                </button>
                <span className="muted"> — {e.reason}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </>
  )
}

function SuccessionLine(
  { result, onSelect, compareWith }:
  {
    result: { line: { position: number; id: string; name: string; note: string }[]
              law_label: string }
    onSelect: (id: string) => void
    compareWith?: string[]
  },
) {
  if (!result.line.length) {
    return <p className="muted">No eligible heir under {result.law_label}.</p>
  }
  return (
    <>
      {result.line.map((c) => {
        const moved = compareWith && compareWith[c.position - 1] !== c.id
        return (
          <div className="succession-line" key={c.id}>
            <span className="pos">{c.position}</span>
            <button className="entity-line" style={{ width: 'auto', padding: 0 }}
                    onClick={() => onSelect(c.id)}>
              <span className="name">{c.name}</span>
            </button>
            {moved && <Badge kind="warning" title="Different from the canonical line">
              moved
            </Badge>}
            {c.note && <span className="muted small">{c.note}</span>}
          </div>
        )
      })}
      <p className="muted small" style={{ marginTop: 8 }}>under {result.law_label}</p>
    </>
  )
}
