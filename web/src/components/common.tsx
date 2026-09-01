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
/** Whether the reader is on the night theme, as a value React can re-render on.
 *
 * The theme is otherwise entirely CSS — `prefers-color-scheme` picks a palette and no
 * component has ever needed to know. The relief raster is the exception and cannot be
 * anything else: it is painted from Python, so the server has to be TOLD which plate
 * to draw, and only the browser knows. Listening rather than reading once, because a
 * reader who flips their system theme should not be left with the other map. */
export function useDarkMode(): boolean {
  const query = '(prefers-color-scheme: dark)'
  const [dark, setDark] = useState(
    () => typeof window !== 'undefined' && window.matchMedia
      ? window.matchMedia(query).matches : false,
  )
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const media = window.matchMedia(query)
    const listen = (e: MediaQueryListEvent) => setDark(e.matches)
    media.addEventListener('change', listen)
    setDark(media.matches)
    return () => media.removeEventListener('change', listen)
  }, [])
  return dark
}

export function usePanZoom(initialScale = 1) {
  const [view, setView] = useState({ x: 0, y: 0, k: initialScale })
  const dragging = useRef<{ x: number; y: number } | null>(null)

  // How many CSS pixels one user unit currently covers. The transform lives in the
  // SVG's own viewBox units, but pointer events arrive in pixels; with
  // preserveAspectRatio "meet" they differ by the fitted scale, and mixing them
  // made drags lag the cursor and put the wheel's "fixed point" slightly off —
  // exactly the arithmetic a fit-to-feature camera cannot survive.
  const pixelsPerUnit = (el: SVGSVGElement): number => {
    const vb = el.viewBox?.baseVal
    if (!vb || !vb.width || !vb.height) return 1
    const rect = el.getBoundingClientRect()
    return Math.min(rect.width / vb.width, rect.height / vb.height) || 1
  }

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    const s = pixelsPerUnit(e.currentTarget)
    dragging.current = { x: e.clientX / s - view.x, y: e.clientY / s - view.y }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!dragging.current) return
    const s = pixelsPerUnit(e.currentTarget)
    setView((v) => ({ ...v, x: e.clientX / s - dragging.current!.x,
                      y: e.clientY / s - dragging.current!.y }))
  }
  const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    dragging.current = null
    e.currentTarget.releasePointerCapture(e.pointerId)
  }
  const onWheel = (e: React.WheelEvent<SVGSVGElement>) => {
    const el = e.currentTarget
    const rect = el.getBoundingClientRect()
    const s = pixelsPerUnit(el)
    const vb = el.viewBox?.baseVal
    // The cursor, in viewBox units — including the letterboxing "meet" centres.
    const dx = vb && vb.width ? (rect.width - vb.width * s) / 2 : 0
    const dy = vb && vb.height ? (rect.height - vb.height * s) / 2 : 0
    const px = (e.clientX - rect.left - dx) / s + (vb ? vb.x : 0)
    const py = (e.clientY - rect.top - dy) / s + (vb ? vb.y : 0)
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
