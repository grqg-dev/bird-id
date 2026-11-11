import { useEffect, useState } from 'react'

/** Minimal History-API routing (no router library). All SPA URLs live under /dash. */
const EVENT = 'birdid:navigate'

export function navigate(path: string, opts: { replace?: boolean } = {}): void {
  if (path === window.location.pathname + window.location.search) return
  if (opts.replace) window.history.replaceState(null, '', path)
  else window.history.pushState(null, '', path)
  window.dispatchEvent(new Event(EVENT))
}

/** Current pathname without a trailing slash; re-renders on navigate() and back/forward. */
export function usePath(): string {
  const read = () => window.location.pathname.replace(/\/+$/, '') || '/'
  const [path, setPath] = useState(read)
  useEffect(() => {
    const on = () => setPath(read())
    window.addEventListener('popstate', on)
    window.addEventListener(EVENT, on)
    return () => {
      window.removeEventListener('popstate', on)
      window.removeEventListener(EVENT, on)
    }
  }, [])
  return path
}

export const dayPath = (day: string) => `/dash/day/${day}`
export const speciesPath = (slug: string) => `/dash/species/${slug}`

export function matchDay(path: string): string | null {
  return /^\/dash\/day\/(\d{4}-\d{2}-\d{2})$/.exec(path)?.[1] ?? null
}

export function matchSpecies(path: string): string | null {
  const m = /^\/dash\/species\/([^/]+)$/.exec(path)
  return m ? decodeURIComponent(m[1]) : null
}
