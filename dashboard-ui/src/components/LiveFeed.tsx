import { useEffect, useState } from 'react'
import type { RecentEvent } from '../lib/types'
import { fmtWhen } from '../lib/format'
import { speciesPath } from '../lib/route'
import { Link } from './Link'
import { Sprite } from './Sprite'

const POLL_MS = 5000

/**
 * Rolling feed of the latest detections (replaces /live). Polls /api/recent
 * every 5 s while the tab is visible; stops while hidden and catches up the
 * moment it is shown again.
 */
export function LiveFeed({ limit = 12, title = 'Live feed' }: { limit?: number; title?: string }) {
  const [events, setEvents] = useState<RecentEvent[]>([])
  const [error, setError] = useState(false)
  const [paused, setPaused] = useState(document.visibilityState === 'hidden')

  useEffect(() => {
    let timer: number | undefined
    let controller: AbortController | undefined
    const tick = () => {
      controller?.abort()
      controller = new AbortController()
      fetch(`/api/v1/recent?limit=${limit}`, { signal: controller.signal })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then((d: { events: RecentEvent[] }) => {
          setEvents(d.events)
          setError(false)
        })
        .catch((e: unknown) => {
          if (e instanceof DOMException && e.name === 'AbortError') return
          setError(true)
        })
    }
    const start = () => {
      tick()
      timer = window.setInterval(tick, POLL_MS)
    }
    const stop = () => {
      window.clearInterval(timer)
      timer = undefined
      controller?.abort()
    }
    const onVis = () => {
      const hidden = document.visibilityState === 'hidden'
      setPaused(hidden)
      if (hidden) stop()
      else if (timer === undefined) start()
    }
    if (document.visibilityState !== 'hidden') start()
    document.addEventListener('visibilitychange', onVis)
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      stop()
    }
  }, [limit])

  return (
    <div className="panel live-feed" data-paused={paused}>
      <h2 className="panel-title">
        {title}
        <span className="sub">
          {error ? 'feed unavailable' : paused ? 'paused (tab hidden)' : 'updates every 5 s'}
        </span>
      </h2>
      {events.length === 0 ? (
        <p className="lbl">nothing heard in the last 24 hours</p>
      ) : (
        <ul className="plain-list live-list">
          {events.map((e) => (
            <li key={`${e.heard_at}-${e.segment_id}-${e.common_name}-${e.start_time}`}>
              <Link className="live-row" href={speciesPath(e.slug)}>
                <Sprite sp={{ name: e.common_name, slug: e.slug, art: e.has_sprite, pixel: false }} size={28} />
                <span className="live-name">{e.common_name}</span>
                <span className="num faint">{e.peak_conf.toFixed(2)}</span>
                <span className="lbl">{fmtWhen(e.heard_at)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
