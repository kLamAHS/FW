/**
 * The relationship graph (§38).
 *
 * A force-directed layout, run here rather than pulled in as a library: the simulation is
 * about thirty lines and a dependency would be the larger cost. It settles rather than
 * running forever, which matters because §99 wants dense views to stay usable.
 *
 * §38 asks for filtering by relationship type, and §69 asks that dense graphs be
 * simplifiable — so the category filters are the primary control, and focusing on one
 * entity narrows the graph to its neighbourhood rather than dimming the rest. Dimming
 * still renders everything; the point is to draw less.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { GraphData } from '../api'
import { ErrorBox, Loading, categoryColour, usePanZoom, useAsync } from '../components/common'

const CATEGORIES = ['kinship', 'feeling', 'politics', 'territory', 'economy',
                    'military', 'culture', 'geography'] as const

interface Node { id: string; name: string; type_key: string; x: number; y: number
                 vx: number; vy: number }

interface Props {
  day: number
  onSelect: (id: string) => void
  selectedId: string | null
  version: number
}

export function GraphView({ day, onSelect, selectedId, version }: Props) {
  const [active, setActive] = useState<Set<string>>(
    new Set(['kinship', 'feeling', 'politics', 'territory']),
  )
  const [focus, setFocus] = useState<string | null>(null)
  const pan = usePanZoom(1)

  const { data, error, loading } = useAsync(
    () => api.graph({
      day,
      categories: [...active].join(','),
      centre: focus ?? undefined,
      hops: 2,
    }),
    [day, [...active].sort().join(','), focus, version],
  )

  const layout = useForceLayout(data)

  if (loading && !data) return <Loading what="Laying out the graph" />
  if (error) return <ErrorBox error={error} />
  if (!data) return null

  const byId = new Map(layout.map((n) => [n.id, n]))

  return (
    <>
      <div className="toolbar">
        <span className="small muted">Show</span>
        {CATEGORIES.map((c) => (
          <button
            key={c}
            aria-pressed={active.has(c)}
            className={active.has(c) ? 'active' : ''}
            onClick={() => setActive((prev) => {
              const next = new Set(prev)
              next.has(c) ? next.delete(c) : next.add(c)
              return next
            })}
            style={{ borderLeft: `4px solid ${categoryColour(c)}` }}
          >
            {c}
          </button>
        ))}
        <span className="spacer" />
        {focus && (
          <button onClick={() => setFocus(null)}>
            Show the whole world
          </button>
        )}
        {selectedId && !focus && (
          <button onClick={() => setFocus(selectedId)}>Focus on the selection</button>
        )}
        <button onClick={pan.reset}>Reset view</button>
      </div>

      <svg className="graph-svg" viewBox="0 0 1000 700" {...pan.handlers}
           role="img" aria-label="Relationship graph">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="22" refY="5"
                  markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--ink-faint)" />
          </marker>
        </defs>
        <g transform={pan.transform}>
          {data.edges.map((e, i) => {
            const a = byId.get(e.source)
            const b = byId.get(e.target)
            if (!a || !b) return null
            const touched = selectedId === e.source || selectedId === e.target
            return (
              <line
                key={i}
                x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                stroke={categoryColour(e.category)}
                strokeWidth={touched ? 2.6 : 1.3}
                strokeOpacity={selectedId && !touched ? 0.18 : 0.65}
                /* Secrecy is dashed as well as coloured — §69. */
                strokeDasharray={e.secret ? '5 4' : undefined}
                markerEnd={e.symmetric ? undefined : 'url(#arrow)'}
              >
                <title>{`${a.name} — ${e.label} → ${b.name}`}
                  {e.strength ? ` (${e.strength.replace(/_/g, ' ')})` : ''}
                  {e.secret ? ' [secret]' : ''}</title>
              </line>
            )
          })}

          {layout.map((n) => {
            const selected = n.id === selectedId
            return (
              <g key={n.id} className="graph-node" transform={`translate(${n.x},${n.y})`}
                 onClick={() => onSelect(n.id)}>
                {/* Shape encodes type, so the graph is readable without colour. */}
                {n.type_key === 'person' ? (
                  <circle r={selected ? 9 : 6} fill={selected ? 'var(--link)' : 'var(--accent)'}
                          stroke="var(--panel)" strokeWidth={2} />
                ) : (
                  <rect x={-6} y={-6} width={12} height={12} rx={2}
                        fill={selected ? 'var(--link)' : 'var(--ink-soft)'}
                        stroke="var(--panel)" strokeWidth={2} />
                )}
                <text x={10} y={4}>{n.name}</text>
                <title>{`${n.name} (${n.type_key})`}</title>
              </g>
            )
          })}
        </g>
      </svg>

      <p className="muted small" style={{ marginTop: 8 }}>
        {data.nodes.length} nodes, {data.edges.length} connections.
        Circles are people, squares are everything else; dashed lines are secret;
        arrows point from the subject to the object. Drag to pan, scroll to zoom.
      </p>
    </>
  )
}

/**
 * A small force simulation: repulsion between all nodes, springs along edges, and a weak
 * pull to the centre. Run for a fixed number of ticks on load rather than continuously —
 * a graph that never stops moving is harder to read, not easier.
 */
function useForceLayout(data: GraphData | null): Node[] {
  const [nodes, setNodes] = useState<Node[]>([])
  const frame = useRef<number>(0)

  const signature = useMemo(
    () => (data ? data.nodes.map((n) => n.id).join('|') + data.edges.length : ''),
    [data],
  )

  useEffect(() => {
    if (!data || data.nodes.length === 0) { setNodes([]); return }

    const width = 1000
    const height = 700
    const working: Node[] = data.nodes.map((n, i) => {
      // Seed on a circle rather than at random: a deterministic start means the same
      // world lays out the same way twice, which makes the view learnable.
      const angle = (i / data.nodes.length) * Math.PI * 2
      const radius = Math.min(width, height) * 0.34
      return {
        ...n,
        x: width / 2 + Math.cos(angle) * radius,
        y: height / 2 + Math.sin(angle) * radius,
        vx: 0, vy: 0,
      }
    })

    const index = new Map(working.map((n, i) => [n.id, i]))
    const links = data.edges
      .map((e) => [index.get(e.source), index.get(e.target)] as const)
      .filter((pair): pair is readonly [number, number] =>
        pair[0] !== undefined && pair[1] !== undefined)

    const TICKS = 260
    let tick = 0
    const step = () => {
      const alpha = 1 - tick / TICKS
      for (let i = 0; i < working.length; i++) {
        const a = working[i]
        for (let j = i + 1; j < working.length; j++) {
          const b = working[j]
          let dx = b.x - a.x
          let dy = b.y - a.y
          let dist2 = dx * dx + dy * dy
          if (dist2 < 1) { dx = (i - j) || 1; dy = 1; dist2 = 2 }
          const force = 2600 / dist2
          const dist = Math.sqrt(dist2)
          a.vx -= (dx / dist) * force
          a.vy -= (dy / dist) * force
          b.vx += (dx / dist) * force
          b.vy += (dy / dist) * force
        }
      }
      for (const [i, j] of links) {
        const a = working[i]
        const b = working[j]
        const dx = b.x - a.x
        const dy = b.y - a.y
        const dist = Math.hypot(dx, dy) || 1
        const force = (dist - 110) * 0.055
        a.vx += (dx / dist) * force
        a.vy += (dy / dist) * force
        b.vx -= (dx / dist) * force
        b.vy -= (dy / dist) * force
      }
      for (const n of working) {
        n.vx += (width / 2 - n.x) * 0.004
        n.vy += (height / 2 - n.y) * 0.004
        n.vx *= 0.82
        n.vy *= 0.82
        n.x += n.vx * alpha
        n.y += n.vy * alpha
      }
      tick += 1
      // The forces settle to whatever scale the node count implies, which for a small
      // world leaves the graph huddled in the middle of a mostly empty canvas. Rescaling
      // to fill the frame each tick keeps it legible at any size without having to tune
      // the constants per world.
      setNodes(fitToFrame(working, width, height))
      if (tick < TICKS) frame.current = requestAnimationFrame(step)
    }
    frame.current = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature])

  return nodes
}

/** Uniformly scale and centre a layout so it fills the frame, preserving its shape. */
function fitToFrame(nodes: Node[], width: number, height: number): Node[] {
  if (nodes.length === 0) return []
  if (nodes.length === 1) {
    return [{ ...nodes[0], x: width / 2, y: height / 2 }]
  }
  const margin = 90
  const xs = nodes.map((n) => n.x)
  const ys = nodes.map((n) => n.y)
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  const spanX = maxX - minX || 1
  const spanY = maxY - minY || 1
  const scale = Math.min((width - margin * 2) / spanX, (height - margin * 2) / spanY)
  const offsetX = (width - spanX * scale) / 2
  const offsetY = (height - spanY * scale) / 2
  return nodes.map((n) => ({
    ...n,
    x: (n.x - minX) * scale + offsetX,
    y: (n.y - minY) * scale + offsetY,
  }))
}
