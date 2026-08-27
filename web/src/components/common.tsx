/** Small shared pieces. */

import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

export function Panel(
  { title, count, children, actions }:
  { title?: string; count?: ReactNode; children: ReactNode; actions?: ReactNode },
) {
  return (
    <section className="panel">
      {title && (
        <h2>
          {title}
          {count !== undefined && <span className="count">{count}</span>}
          {actions && <span style={{ marginLeft: 'auto' }}>{actions}</span>}
        </h2>
      )}
      {children}
    </section>
  )
}

export function Loading({ what = 'Loading' }: { what?: string }) {
  return <div className="spinner">{what}…</div>
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}

export function ErrorBox({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error)
  return <div className="error-box">{message}</div>
}

/** Severity carries a glyph as well as a colour, per §69. */
export const SEVERITY_GLYPH: Record<string, string> = {
  error: '!', warning: '△', notice: '·',
}

export function Badge(
  { kind, children, title }:
  { kind?: string; children: ReactNode; title?: string },
) {
  return <span className={`badge ${kind ?? ''}`} title={title}>{children}</span>
}

export function TypeChip({ type }: { type: string }) {
  return <span className="type-chip">{type.replace(/_/g, ' ')}</span>
}

/**
 * `useAsync` — the smallest thing that covers every data-loading case in this app.
 *
 * Guards against the out-of-order response: dragging the timeline slider fires a request
 * per step, and without the cancellation flag an early slow response can land after a late
 * fast one and repaint the world at the wrong date.
 */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: unknown[],
): { data: T | null; error: unknown; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fnRef.current()
      .then((result) => { if (!cancelled) { setData(result); setError(null) } })
      .catch((err) => { if (!cancelled) { setError(err); setData(null) } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { data, error, loading, reload: () => setNonce((n) => n + 1) }
}

/** Debounce, so typing in the search box does not fire a request per keystroke. */
export function useDebounced<T>(value: T, delay = 220): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return settled
}

/**
 * Pan and zoom for the SVG surfaces.
 *
 * Returned as a transform string rather than as scroll position, because §99 wants dense
 * views to stay usable and transforming one group is far cheaper than moving thousands of
 * nodes.
 */
export function usePanZoom(initialScale = 1) {
  const [view, setView] = useState({ x: 0, y: 0, k: initialScale })
  const dragging = useRef<{ x: number; y: number } | null>(null)

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    dragging.current = { x: e.clientX - view.x, y: e.clientY - view.y }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!dragging.current) return
    setView((v) => ({ ...v, x: e.clientX - dragging.current!.x, y: e.clientY - dragging.current!.y }))
  }
  const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    dragging.current = null
    e.currentTarget.releasePointerCapture(e.pointerId)
  }
  const onWheel = (e: React.WheelEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const px = e.clientX - rect.left
    const py = e.clientY - rect.top
    setView((v) => {
      const k = Math.min(6, Math.max(0.08, v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)))
      // Keep the point under the cursor fixed while zooming.
      return { k, x: px - ((px - v.x) / v.k) * k, y: py - ((py - v.y) / v.k) * k }
    })
  }

  return {
    view,
    setView,
    reset: () => setView({ x: 0, y: 0, k: initialScale }),
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerLeave: onPointerUp, onWheel },
    transform: `translate(${view.x},${view.y}) scale(${view.k})`,
  }
}

/** Stable colour per category, so the same relationship type reads alike everywhere. */
export const CATEGORY_COLOURS: Record<string, string> = {
  kinship: '#7a5c3e',
  feeling: '#a2456a',
  politics: '#2f5d8a',
  territory: '#3f6b52',
  geography: '#557a8a',
  economy: '#8a6113',
  culture: '#6b3f7a',
  military: '#a2332c',
  knowledge: '#4a5058',
  history: '#5c5347',
  identity: '#4a5058',
  other: '#7c8590',
}

export function categoryColour(category: string): string {
  return CATEGORY_COLOURS[category] ?? CATEGORY_COLOURS.other
}
