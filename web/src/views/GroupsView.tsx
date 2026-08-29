/**
 * Groups of people (§54), and what is inside a place (§2, §12).
 *
 * A world is not only its noble houses. This is the view that answers "who is in the
 * North?" — the houses seated in its castles, the guilds working its towns, the orders
 * that merely range across it, and the minor houses under each banner. Two panels
 * because those are two different questions: pick a group and see who belongs to it,
 * or pick a place and see everything and everyone inside it.
 */

import { useState } from 'react'
import { api } from '../api'
import type { PlaceNode } from '../api'
import { Badge, ErrorBox, Loading, Panel, TypeChip, useAsync } from '../components/common'

interface Props {
  day: number
  onSelect: (id: string) => void
  version: number
}

export function GroupsView({ day, onSelect, version }: Props) {
  const groups = useAsync(() => api.groups(day), [day, version])
  const places = useAsync(
    () => api.entities({ type_key: 'region', at: day }), [day, version])
  const [openGroup, setOpenGroup] = useState<string | null>(null)
  const [openPlace, setOpenPlace] = useState<string | null>(null)

  const detail = useAsync(
    () => (openGroup ? api.group(openGroup, day) : Promise.resolve(null)),
    [openGroup, day, version],
  )
  const contents = useAsync(
    () => (openPlace ? api.placeContents(openPlace, day) : Promise.resolve(null)),
    [openPlace, day, version],
  )

  if (groups.loading && !groups.data) return <Loading what="Gathering the groups" />
  if (groups.error) return <ErrorBox error={groups.error} />

  return (
    <div className="two-column">
      <Panel title="Groups of people" count={groups.data?.length ?? 0}>
        {(groups.data ?? []).length === 0 && (
          <p className="muted small">
            No houses, guilds or orders yet. Anything with members belongs here — add a
            guild, an order, a tribe or a company from “+ New”.
          </p>
        )}
        {(groups.data ?? []).map((g) => (
          <div key={g.entity.id}>
            <button className="entity-line"
                    onClick={() => setOpenGroup(
                      openGroup === g.entity.id ? null : g.entity.id)}>
              <span className="name">{g.entity.name}</span>
              <TypeChip type={g.entity.type_key} />
              <span className="desc">
                {g.seats.length > 0
                  ? g.seats.map((s) => `${s.how} ${s.name}`).join(', ')
                  : g.entity.summary}
              </span>
              {g.members > 0 && <Badge>{g.members} sworn</Badge>}
              {g.branches > 0 && <Badge>{g.branches} lesser</Badge>}
            </button>

            {openGroup === g.entity.id && (
              <div style={{ paddingLeft: 14, borderLeft: '2px solid var(--accent)',
                            margin: '4px 0 10px' }}>
                {detail.loading && <Loading what="Reading the roster" />}
                {detail.data && <GroupDetailBody detail={detail.data}
                                                 onSelect={onSelect} />}
              </div>
            )}
          </div>
        ))}
      </Panel>

      <Panel title="What is inside a place">
        <div className="toolbar" style={{ flexWrap: 'wrap' }}>
          {(places.data ?? []).map((p) => (
            <button key={p.id} className={openPlace === p.id ? 'active' : ''}
                    onClick={() => setOpenPlace(p.id)}>
              {p.name}
            </button>
          ))}
        </div>
        {!openPlace && (
          <p className="muted small">
            Choose a region to see its cities, its towns and everyone seated in them.
          </p>
        )}
        {contents.loading && <Loading what="Walking the place" />}
        {contents.data && (
          <>
            {contents.data.within.length > 0 && (
              <p className="small muted">
                within {contents.data.within.map((e) => e.name).join(' → ')}
              </p>
            )}
            <PlaceTree node={contents.data.tree} onSelect={onSelect} />
            {contents.data.groups.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <h3 style={{ marginBottom: 5 }}>Everyone in here</h3>
                {contents.data.groups.map(({ entity, how }) => (
                  <button key={entity.id} className="entity-line"
                          onClick={() => onSelect(entity.id)}>
                    <span className="name">{entity.name}</span>
                    <TypeChip type={entity.type_key} />
                    <span className="desc">{how}</span>
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </Panel>
    </div>
  )
}

function GroupDetailBody(
  { detail, onSelect }:
  { detail: NonNullable<Awaited<ReturnType<typeof api.group>>>
    onSelect: (id: string) => void },
) {
  const line = (id: string, name: string, type: string, note: string) => (
    <button key={id} className="entity-line" onClick={() => onSelect(id)}>
      <span className="name">{name}</span>
      <TypeChip type={type} />
      <span className="desc">{note}</span>
    </button>
  )

  return (
    <>
      {detail.seats.length > 0 && (
        <p className="small muted">
          {detail.seats.map((s) => `${s.how} ${s.entity.name}`).join(' · ')}
        </p>
      )}
      {detail.members.length > 0 && (
        <>
          <div className="small muted" style={{ marginTop: 6 }}>Who belongs</div>
          {detail.members.map((m) =>
            line(m.entity.id, m.entity.name, m.entity.type_key,
                 m.note ? `${m.relation} — ${m.note}` : m.relation))}
        </>
      )}
      {detail.branches.length > 0 && (
        <>
          <div className="small muted" style={{ marginTop: 6 }}>
            Lesser bodies under it
          </div>
          {detail.branches.map((b) =>
            line(b.entity.id, `${'· '.repeat(b.depth - 1)}${b.entity.name}`,
                 b.entity.type_key,
                 b.depth === 1 ? 'sworn directly' : `${b.depth} steps down`))}
        </>
      )}
      {detail.members.length === 0 && detail.branches.length === 0 && (
        <p className="muted small">
          Nobody recorded yet — connect people with “member of”, or a lesser house
          with “a branch of”.
        </p>
      )}
    </>
  )
}

/** The containment tree, indented. Places recurse; everything else is a leaf. */
function PlaceTree(
  { node, onSelect }: { node: PlaceNode; onSelect: (id: string) => void },
) {
  return (
    <div style={{ paddingLeft: node.depth === 0 ? 0 : 14 }}>
      <button className="entity-line" onClick={() => onSelect(node.entity.id)}>
        <span className="name">{node.entity.name}</span>
        <TypeChip type={node.settlement_type ?? node.entity.type_key} />
        <span className="desc">{node.entity.summary}</span>
        {node.inside > 0 && <Badge>{node.inside} inside</Badge>}
      </button>
      {node.children.map((child) => (
        <PlaceTree key={child.entity.id} node={child} onSelect={onSelect} />
      ))}
      {[...node.groups, ...node.people, ...node.other].map((e) => (
        <button key={e.id} className="entity-line"
                style={{ marginLeft: 14 }} onClick={() => onSelect(e.id)}>
          <span className="name">{e.name}</span>
          <TypeChip type={e.type_key} />
          <span className="desc">{e.summary}</span>
        </button>
      ))}
    </div>
  )
}
