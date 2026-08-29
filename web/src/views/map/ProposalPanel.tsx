/**
 * Answering a map that does not exist yet (§66, §67).
 *
 * The generator proposes; the writer disposes. This is where that happens: every
 * feature the map wants to draw, grouped by what it is, each with the sentence
 * explaining why it is there — because a number a novelist cannot interrogate is a
 * number they cannot use.
 *
 * Two defaults carry the whole principle. Putting the writer's own places on the map
 * is expected, so those arrive ticked. Inventing a place is a suggestion, so those
 * arrive unticked and stay off unless someone says otherwise. Nothing is written until
 * the writer presses the button, and what is written is exactly what they looked at.
 */

import { useMemo, useState } from 'react'
import type { MapDecision, MapPlan, PlannedFeature } from '../../api'

const NOUNS: Record<string, string> = {
  coast: 'Coastline', island: 'Islands', region: 'Regions', range: 'Mountain ranges',
  hills: 'Hill country', sea: 'Seas', water: 'Waters', lake: 'Lakes',
  river: 'Rivers', natural: 'Forests and marshes', settlement: 'Settlements',
  castle: 'Castles', ruin: 'Ruins', road: 'Roads',
}

interface Props {
  plan: MapPlan
  busy: boolean
  /** Held by the map rather than here, so the overlay draws the same answer. */
  accepted: Record<string, boolean>
  setAccepted: (next: (was: Record<string, boolean>) => Record<string, boolean>) => void
  onApply: (decisions: MapDecision[]) => void
  onDiscard: () => void
}

export function ProposalPanel({ plan, busy, accepted, setAccepted, onApply,
                                onDiscard }: Props) {
  const [names, setNames] = useState<Record<string, string>>({})
  const [pinned, setPinned] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<Set<string>>(new Set())

  const groups = useMemo(() => {
    const byKind = new Map<string, PlannedFeature[]>()
    for (const feature of plan.features) {
      const list = byKind.get(feature.kind) ?? []
      list.push(feature)
      byKind.set(feature.kind, list)
    }
    return [...byKind.entries()]
  }, [plan])

  // A road to a town that has been turned down is a road to nowhere, so it greys out
  // the moment the town does — the writer sees the consequence before they commit.
  const blocked = useMemo(() => {
    const out = new Set<string>()
    let changed = true
    while (changed) {
      changed = false
      for (const feature of plan.features) {
        if (out.has(feature.id)) continue
        const lost = feature.depends_on.find((id) => !accepted[id] || out.has(id))
        if (lost) { out.add(feature.id); changed = true }
      }
    }
    return out
  }, [plan, accepted])

  const live = plan.features.filter((f) => accepted[f.id] && !blocked.has(f.id))

  const setAll = (kind: string, value: boolean) =>
    setAccepted((was) => {
      const next = { ...was }
      for (const feature of plan.features) if (feature.kind === kind) next[feature.id] = value
      return next
    })

  const apply = () => onApply(plan.features.map((feature) => ({
    feature_id: feature.id,
    accept: Boolean(accepted[feature.id]) && !blocked.has(feature.id),
    name: names[feature.id] ?? null,
    pinned: pinned.has(feature.id),
  })))

  return (
    <div className="proposal">
      <header className="proposal-head">
        <div>
          <strong>{plan.summary}</strong>
          <p className="muted small">
            Nothing has been written yet. Untick anything you do not want, rename what
            you do, then keep the rest.
          </p>
        </div>
        <div className="proposal-actions">
          <button className="primary" onClick={apply} disabled={busy}>
            {busy ? 'Writing…' : `Keep ${live.length} of ${plan.features.length}`}
          </button>
          <button onClick={onDiscard} disabled={busy}>Discard</button>
        </div>
      </header>

      {plan.findings.length > 0 && (
        <ul className="findings">
          {plan.findings.map((finding, n) => (
            <li key={n} className={finding.severity}>
              {finding.message}
              {finding.quotes.length > 0 && <em> — “{finding.quotes.join('”, “')}”</em>}
            </li>
          ))}
        </ul>
      )}

      {plan.retiring.length > 0 && (
        <p className="muted small">
          {plan.retiring.filter((r) => r.writer_touched).length > 0
            ? `${plan.retiring.filter((r) => r.writer_touched).length} things you have since
               made your own will be left alone rather than redrawn. `
            : ''}
          {plan.retiring.filter((r) => !r.writer_touched).length} from the last map are
          not in this one.
        </p>
      )}

      {groups.map(([kind, features]) => (
        <section key={kind} className="proposal-group">
          <h4>
            <button className="link" onClick={() => setOpen((was) => {
              const next = new Set(was)
              next.has(kind) ? next.delete(kind) : next.add(kind)
              return next
            })}>
              {open.has(kind) ? '▾' : '▸'} {NOUNS[kind] ?? kind}
              <span className="muted"> ({features.filter((f) => accepted[f.id]).length}
                /{features.length})</span>
            </button>
            <span className="proposal-bulk">
              <button className="link" onClick={() => setAll(kind, true)}>all</button>
              <button className="link" onClick={() => setAll(kind, false)}>none</button>
            </span>
          </h4>
          {open.has(kind) && (
            <ul className="proposal-list">
              {features.map((feature) => (
                <li key={feature.id}
                    className={blocked.has(feature.id) ? 'blocked' : undefined}>
                  <div className="proposal-row">
                  <label>
                    <input type="checkbox"
                           checked={Boolean(accepted[feature.id]) && !blocked.has(feature.id)}
                           disabled={blocked.has(feature.id)}
                           onChange={(e) => setAccepted(
                             (was) => ({ ...was, [feature.id]: e.target.checked }))} />
                    {feature.renameable ? (
                      <input className="proposal-name"
                             value={names[feature.id] ?? feature.name}
                             aria-label={`Name for ${feature.name}`}
                             onChange={(e) => setNames(
                               (was) => ({ ...was, [feature.id]: e.target.value }))} />
                    ) : (
                      <strong>{feature.name}</strong>
                    )}
                    {!feature.renameable && <span className="tag">yours</span>}
                  </label>
                  <button className="link small"
                          title="Keep this exactly as it is; the generator will not touch it again"
                          onClick={() => setPinned((was) => {
                            const next = new Set(was)
                            next.has(feature.id) ? next.delete(feature.id)
                                                 : next.add(feature.id)
                            return next
                          })}>
                    {pinned.has(feature.id) ? '📌 pinned' : 'pin'}
                  </button>
                  </div>
                  <p className="muted small">
                    {blocked.has(feature.id)
                      ? 'needs something you have turned down'
                      : feature.why.join('; ')}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  )
}

/** The proposal drawn over the map, dashed and translucent: not yet real. */
export function ProposalOverlay({ plan, accepted }: {
  plan: MapPlan
  accepted: Record<string, boolean>
}) {
  return (
    <g className="proposal-overlay" opacity={0.72}>
      {plan.features.filter((f) => accepted[f.id]).flatMap((feature) =>
        feature.shapes.map((shape, n) => {
          const key = `${feature.id}:${n}`
          if (shape.kind === 'polygon') {
            return (shape.coordinates as number[][][]).map((ring, r) => (
              <polygon key={`${key}:${r}`}
                       points={ring.map((p) => `${p[0]},${p[1]}`).join(' ')}
                       fill={shape.style.fill ?? '#b6bb92'} fillOpacity={0.35}
                       stroke="#5c6b52" strokeWidth={1.4} strokeDasharray="7 5" />
            ))
          }
          if (shape.kind === 'line') {
            return (
              <polyline key={key}
                        points={(shape.coordinates as number[][])
                          .map((p) => `${p[0]},${p[1]}`).join(' ')}
                        fill="none" stroke={shape.style.stroke ?? '#4a7fa5'}
                        strokeWidth={2.4} strokeDasharray="6 4"
                        strokeLinecap="round" strokeLinejoin="round" />
            )
          }
          const [x, y] = shape.coordinates as number[]
          // The same shapes the drawn map uses, so a writer reviewing nine castles and
          // eighteen towns can tell at a glance which pin is which.
          return (
            <g key={key}>
              {shape.layer === 'castles' ? (
                <rect x={x - 4.7} y={y - 4.7} width={9.4} height={9.4}
                      transform={`rotate(45 ${x} ${y})`}
                      fill="none" stroke="#7a2b2b"
                      strokeWidth={1.4} strokeDasharray="3 3" />
              ) : (
                <circle cx={x} cy={y} r={6} fill="none" stroke="#7a2b2b"
                        strokeWidth={1.4} strokeDasharray="3 3" />
              )}
              <circle cx={x} cy={y} r={2.5} fill="#7a2b2b" />
            </g>
          )
        }))}
    </g>
  )
}
