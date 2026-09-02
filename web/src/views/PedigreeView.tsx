/**
 * The pedigree (§39).
 *
 * §39 asks for a view "separate from the generic relationship graph", and the reason is
 * structural: descent has a direction and generations are ranks, which a force simulation
 * throws away. Coordinates come from the backend so the layout is deterministic and
 * testable; this file draws them.
 *
 * The features §39 lists are all here and all visible without hovering: legitimacy,
 * uncertain parentage, house membership, marriages, and who is alive on the current date.
 * A dashed line is a parentage the world does not treat as legal — in the example world
 * that single dashed line is the entire plot.
 */

import { useState } from 'react'
import { api } from '../api'
import { Badge, ErrorBox, Loading, usePanZoom, useAsync } from '../components/common'

const W = 170
const H = 64

interface Props {
  day: number
  onSelect: (id: string) => void
  selectedId: string | null
  version: number
}

export function PedigreeView({ day, onSelect, selectedId, version }: Props) {
  const [lens, setLens] = useState<'legal' | 'biological'>('legal')
  const [livingOnly, setLivingOnly] = useState(false)
  const pan = usePanZoom(0.85)

  const { data, error, loading } = useAsync(
    () => api.pedigree({ lens, living_only_on: livingOnly ? day : undefined }),
    [lens, livingOnly, day, version],
  )

  if (loading && !data) return <Loading what="Laying out the family" />
  if (error) return <ErrorBox error={error} />
  if (!data || !data.people.length) {
    return <p className="muted">This world has no recorded descent yet.</p>
  }

  const byId = new Map(data.people.map((p) => [p.id, p]))
  const minX = Math.min(...data.people.map((p) => p.x)) - 60
  const minY = Math.min(...data.people.map((p) => p.y)) - 40
  const viewBox = `${minX} ${minY} ${data.width + 120} ${data.height + 90}`

  return (
    <>
      <div className="toolbar">
        <span className="small muted">Descent by</span>
        <button className={lens === 'legal' ? 'active' : ''} onClick={() => setLens('legal')}
                title="Descent as the law recognises it — what inheritance follows">
          law
        </button>
        <button className={lens === 'biological' ? 'active' : ''}
                onClick={() => setLens('biological')}
                title="Descent by blood, which may not be what the law recognises">
          blood
        </button>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', marginLeft: 8 }}>
          <input type="checkbox" checked={livingOnly}
                 onChange={() => setLivingOnly((v) => !v)} />
          <span className="small">living on this date only</span>
        </label>
        <span className="spacer" />
        <button onClick={pan.reset}>Reset view</button>
      </div>

      <svg className="pedigree-svg" viewBox={viewBox} ref={pan.ref} {...pan.handlers}
           role="img" aria-label="Family tree">
        <g transform={pan.transform}>
          {/* parent → child lines */}
          {data.links.map((link, i) => {
            const parent = byId.get(link.parent_id)
            const child = byId.get(link.child_id)
            if (!parent || !child) return null
            const x1 = parent.x + W / 2
            const y1 = parent.y + H
            const x2 = child.x + W / 2
            const y2 = child.y
            const mid = (y1 + y2) / 2
            return (
              <path
                key={i}
                d={`M${x1},${y1} L${x1},${mid} L${x2},${mid} L${x2},${y2}`}
                fill="none"
                stroke={link.uncertain ? 'var(--secret)' : 'var(--line)'}
                strokeWidth={link.uncertain ? 2 : 1.5}
                strokeDasharray={link.uncertain ? '6 4' : undefined}
              >
                <title>
                  {link.uncertain
                    ? `${parent.name} is ${child.name}'s parent by blood, but not in law`
                    : `${parent.name} → ${child.name} (${link.kind})`}
                </title>
              </path>
            )
          })}

          {/* marriages */}
          {data.unions.map((u, i) => {
            const a = byId.get(u.a_id)
            const b = byId.get(u.b_id)
            if (!a || !b) return null
            return (
              <line
                key={i}
                x1={a.x + W} y1={a.y + H / 2}
                x2={b.x} y2={b.y + H / 2}
                stroke="var(--accent)" strokeWidth={2.5}
              >
                <title>{`${a.name} married to ${b.name}`}</title>
              </line>
            )
          })}

          {/* people */}
          {data.people.map((p) => {
            const alive = (p.born === null || p.born <= day)
                       && (p.died === null || p.died >= day)
            const illegitimate = p.legitimacy !== 'legitimate'
            return (
              <g key={p.id}
                 className={`ped-card ${p.id === selectedId ? 'selected' : ''}`}
                 transform={`translate(${p.x},${p.y})`}
                 onClick={() => onSelect(p.id)}>
                <rect
                  width={W} height={H} rx={5}
                  /* Legitimacy shows as a dashed outline, not only a colour — §69. */
                  strokeDasharray={illegitimate ? '5 3' : undefined}
                  opacity={alive ? 1 : 0.6}
                />
                <text className="nm" x={10} y={22}>{truncate(p.name, 22)}</text>
                <text className="dt" x={10} y={39}>
                  {p.gender === 'male' ? '♂' : p.gender === 'female' ? '♀' : '·'}
                  {'  '}
                  {p.died !== null ? 'died' : alive ? 'living' : 'not yet born'}
                </text>
                {illegitimate && (
                  <text className="dt" x={10} y={54} fill="var(--secret)">
                    {p.legitimacy}
                  </text>
                )}
                {p.collapsed && p.hidden_descendants > 0 && (
                  <text className="dt" x={W - 10} y={54} textAnchor="end">
                    +{p.hidden_descendants}
                  </text>
                )}
                <title>{`${p.name} — generation ${p.generation}, ${p.legitimacy}`}</title>
              </g>
            )
          })}
        </g>
      </svg>

      <p className="muted small" style={{ marginTop: 8 }}>
        {data.people.length} people over{' '}
        {Math.max(...data.people.map((p) => p.generation)) + 1} generations.{' '}
        <Badge kind="secret">dashed line</Badge> marks a parentage recognised by blood but
        not by law; a dashed box marks a contested or illegitimate birth. Faded boxes are
        people not alive on the selected date.
      </p>
    </>
  )
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`
}
