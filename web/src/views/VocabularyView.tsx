/**
 * The words this world is described in (§60).
 *
 * "Users should be able to customize almost everything… avoid locking the software to
 * European medieval fantasy." Both writers — `add_entity_type` and `add_predicate` —
 * have existed since the world model did, and the README said, accurately, that they
 * worked "through the API or the CLI rather than a screen". So a science-fiction or a
 * nomadic-steppe world was possible, and possible only from a Python prompt.
 *
 * What a writer makes here is not a second-class extension: a custom type appears in the
 * entity form, the map's reading, the query builder and the dashboard, because every one
 * of those is built from this same table. That is the whole point of the section, and it
 * is why this screen is a form over the vocabulary rather than a plugin system.
 */

import { useState } from 'react'
import { api } from '../api'
import type { Vocabulary } from '../api'
import { Badge, ErrorBox, Panel } from '../components/common'

interface Props {
  vocabulary: Vocabulary | null
  onMutate: () => void
}

const KINDS = [
  { key: 'rel', label: 'points at something else' },
  { key: 'prop', label: 'holds a value' },
]

export function VocabularyView({ vocabulary, onMutate }: Props) {
  const [error, setError] = useState<string | null>(null)
  const [type, setType] = useState({ key: '', label: '', plural: '', category: '' })
  const [predicate, setPredicate] = useState({
    key: '', label: '', kind: 'rel', inverse_key: '', category: '', description: '',
  })

  const guard = async (work: () => Promise<unknown>, done: () => void) => {
    setError(null)
    try {
      await work()
      done()
      onMutate()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const types = vocabulary?.entity_types ?? []
  const predicates = vocabulary?.predicates ?? []
  const byCategory = <T extends { category: string }>(rows: T[]) => {
    const out = new Map<string, T[]>()
    for (const row of rows) out.set(row.category, [...(out.get(row.category) ?? []), row])
    return [...out.entries()].sort(([a], [b]) => a.localeCompare(b))
  }

  return (
    <>
      <Panel title="The words your world is described in">
        <p className="muted small">
          Everything here behaves exactly like the kinds and relationships that came with
          the application — the entity form, the map, the questions you can ask and the
          dashboard are all built from this list, so what you add appears in all of them.
        </p>
        {error && <ErrorBox error={error} />}
      </Panel>

      <div className="grid wide">
        <Panel title="Kinds of thing" count={types.length}>
          <div className="form-stack">
            <div className="toolbar" style={{ flexWrap: 'wrap' }}>
              <input value={type.label} placeholder="Star system"
                     onChange={(e) => setType({
                       ...type, label: e.target.value,
                       key: type.key || e.target.value.toLowerCase().replace(/ /g, '_'),
                     })} />
              <input value={type.key} placeholder="star_system"
                     onChange={(e) => setType({ ...type, key: e.target.value })} />
              <input value={type.plural} placeholder="star systems (optional)"
                     onChange={(e) => setType({ ...type, plural: e.target.value })} />
              <input value={type.category} placeholder="category (optional)"
                     onChange={(e) => setType({ ...type, category: e.target.value })} />
              <button className="active"
                      disabled={!type.label.trim() || !type.key.trim()}
                      onClick={() => void guard(
                        () => api.addEntityType({
                          key: type.key.trim(), label: type.label.trim(),
                          plural: type.plural.trim(),
                          category: type.category.trim() || 'other',
                        }),
                        () => setType({ key: '', label: '', plural: '', category: '' }),
                      )}>
                Add it
              </button>
            </div>
            <p className="muted small">
              The key is what every fact is stored against, so it is the one part that
              cannot be renamed later without rewriting them all.
            </p>
          </div>

          {byCategory(types).map(([category, rows]) => (
            <div key={category} style={{ marginTop: 8 }}>
              <div className="small muted">{category}</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {rows.map((t) => (
                  <Badge key={t.key}>{t.plural}</Badge>
                ))}
              </div>
            </div>
          ))}
        </Panel>

        <Panel title="Relationships and properties" count={predicates.length}>
          <div className="form-stack">
            <div className="toolbar" style={{ flexWrap: 'wrap' }}>
              <input value={predicate.label} placeholder="orbits"
                     onChange={(e) => setPredicate({
                       ...predicate, label: e.target.value,
                       key: predicate.key
                         || e.target.value.toLowerCase().replace(/ /g, '_'),
                     })} />
              <input value={predicate.key} placeholder="orbits"
                     onChange={(e) => setPredicate({ ...predicate, key: e.target.value })} />
              <select value={predicate.kind}
                      onChange={(e) => setPredicate({ ...predicate, kind: e.target.value })}>
                {KINDS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
              </select>
              {predicate.kind === 'rel' && (
                <select value={predicate.inverse_key}
                        onChange={(e) => setPredicate({
                          ...predicate, inverse_key: e.target.value,
                        })}>
                  <option value="">no opposite</option>
                  {predicates.filter((p) => p.kind === 'rel').map((p) => (
                    <option key={p.key} value={p.key}>the opposite of {p.label}</option>
                  ))}
                </select>
              )}
              <input value={predicate.category} placeholder="category (optional)"
                     onChange={(e) => setPredicate({
                       ...predicate, category: e.target.value,
                     })} />
              <button className="active"
                      disabled={!predicate.label.trim() || !predicate.key.trim()}
                      onClick={() => void guard(
                        () => api.addPredicate({
                          key: predicate.key.trim(), label: predicate.label.trim(),
                          kind: predicate.kind,
                          inverse_key: predicate.inverse_key || null,
                          category: predicate.category.trim() || 'other',
                          description: predicate.description,
                        }),
                        () => setPredicate({
                          key: '', label: '', kind: 'rel', inverse_key: '',
                          category: '', description: '',
                        }),
                      )}>
                Add it
              </button>
            </div>
            <p className="muted small">
              Naming the opposite means the fact shows on both entities' pages, and you
              never enter it twice (§77).
            </p>
          </div>

          {byCategory(predicates).map(([category, rows]) => (
            <div key={category} style={{ marginTop: 8 }}>
              <div className="small muted">{category}</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {rows.map((p) => (
                  <Badge key={p.key}>{p.label}</Badge>
                ))}
              </div>
            </div>
          ))}
        </Panel>
      </div>
    </>
  )
}
