import { useEffect, useMemo, useState } from 'react'
import type { SpeciesDetail } from '../lib/types'
import { fmtDay, fmtHour, fmtInt, fmtWhen } from '../lib/format'
import { useTheme } from '../theme'
import { ClipPlayer } from './ClipPlayer'
import { Link } from './Link'
import { Sparkline } from './Sparkline'
import { Sprite } from './Sprite'
import { TipProvider, useTip } from './Tip'

/** Species page inside the SPA (/dash/species/<slug>); replaces the old /bird/<slug>. */
export function SpeciesPage({ slug }: { slug: string }) {
  return (
    <TipProvider>
      <SpeciesBody slug={slug} />
    </TipProvider>
  )
}

function SpeciesBody({ slug }: { slug: string }) {
  const { theme, toggle } = useTheme()
  const tip = useTip()
  const [data, setData] = useState<SpeciesDetail | null>(null)
  const [more, setMore] = useState<SpeciesDetail['clips']>([])
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setData(null)
    setMore([])
    setPage(1)
    fetch(`/api/dash/species/${encodeURIComponent(slug)}`, { signal: controller.signal })
      .then((r) => {
        if (r.status === 404) throw new Error('no detections for this species')
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json() as Promise<SpeciesDetail>
      })
      .then((d) => {
        setData(d)
        setHasMore(d.has_more)
      })
      .catch((e: unknown) => {
        if (e instanceof DOMException && e.name === 'AbortError') return
        setError(String(e instanceof Error ? e.message : e))
      })
    return () => controller.abort()
  }, [slug])

  const loadMore = () => {
    const next = page + 1
    fetch(`/api/dash/species/${encodeURIComponent(slug)}?page=${next}`)
      .then((r) => r.json() as Promise<SpeciesDetail>)
      .then((d) => {
        setMore((m) => [...m, ...d.clips])
        setPage(next)
        setHasMore(d.has_more)
      })
      .catch((e) => setError(String(e)))
  }

  const series = useMemo(() => {
    if (!data) return []
    const keys = Object.keys(data.daily).sort()
    if (!keys.length) return []
    const out: number[] = []
    const end = new Date(keys[keys.length - 1] + 'T12:00:00').getTime()
    for (let t = new Date(keys[0] + 'T12:00:00').getTime(); t <= end; t += 86_400_000) {
      out.push(data.daily[new Date(t).toISOString().slice(0, 10)] ?? 0)
    }
    return out
  }, [data])

  const back = (
    <Link className="lbl" href="/dash">
      ← dashboard
    </Link>
  )

  if (error) {
    return (
      <div className="wrap">
        <div className="panel" style={{ marginTop: 60, textAlign: 'center' }}>
          <p>couldn’t load this species ({error})</p>
          {back}
        </div>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="wrap">
        <div className="loading lbl">listening…</div>
      </div>
    )
  }

  const maxH = Math.max(1, ...data.hourly)
  const info = data.info
  const clips = [...data.clips, ...more]

  return (
    <div className="wrap species-page">
      <header className="masthead">
        <div>{back}</div>
        <div className="masthead-right">
          <button className="chip theme-toggle" onClick={toggle}>
            {theme === 'game' ? 'Clean mode' : 'Game mode'}
          </button>
        </div>
      </header>

      <div className="panel species-head">
        <Sprite sp={data} size={120} />
        <div>
          <h1 className="dex-entry-name">{data.name}</h1>
          <div className="dex-entry-sci">{data.sci}</div>
          {info?.group && <div className="lbl" style={{ marginTop: 4 }}>{info.group}</div>}
          {data.status === 'unlikely' && (
            <div className="lbl" style={{ marginTop: 4 }}>
              marked unlikely at this yard — hidden from the main dashboard by default
            </div>
          )}
          <div className="entry-stats num" style={{ marginTop: 10 }}>
            <span>×{fmtInt(data.total)} detections</span>
            <span>{data.days} days</span>
            <span>peak {data.peak.toFixed(2)}</span>
          </div>
          <div className="entry-stats lbl">
            <span>first {fmtWhen(data.first)}</span>
            <span>last {fmtWhen(data.last)}</span>
          </div>
        </div>
      </div>

      <div className="grid-2" style={{ marginTop: 16 }}>
        <div className="panel">
          <h2 className="panel-title">Field notes</h2>
          {info?.sound && <p className="entry-blurb">“{info.sound}”</p>}
          {info?.note && <p className="entry-blurb">{info.note}</p>}
          {(info?.habitat || info?.season) && (
            <p className="entry-blurb faint">
              {[info.habitat, info.season].filter(Boolean).join(' · ')}
            </p>
          )}
          {!info && <p className="lbl">no field notes written for this species yet</p>}
        </div>

        <div className="panel">
          <h2 className="panel-title">
            When it calls
            <span className="sub">all-time, by hour</span>
          </h2>
          <svg viewBox="0 0 240 56" width="100%" role="img" aria-label="Calls by hour of day">
            {data.hourly.map((c, h) => {
              const bh = Math.max(c > 0 ? 2 : 0, (c / maxH) * 46)
              return (
                <rect
                  key={h}
                  x={h * 10 + 1}
                  y={50 - bh}
                  width={8}
                  height={bh}
                  rx={1.5}
                  fill="var(--heat3)"
                  onMouseMove={(e) => tip.show(e, `${fmtHour(h)} · ${fmtInt(c)} calls`)}
                  onMouseLeave={tip.hide}
                />
              )
            })}
            <line x1={0} x2={240} y1={50.5} y2={50.5} stroke="var(--line-strong)" />
          </svg>
          <div className="lbl" style={{ margin: '10px 0 4px' }}>day by day</div>
          <Sparkline data={series} width={400} height={34} fluid />
        </div>
      </div>

      <div className="panel" style={{ marginTop: 16 }}>
        <h2 className="panel-title">
          Clips
          <span className="sub">best first · recordings still on disk</span>
        </h2>
        {clips.length === 0 ? (
          <p className="lbl">no recordings kept on this machine</p>
        ) : (
          <ul className="plain-list clip-list">
            {clips.map((c) => (
              <li key={`${c.seg}-${c.s}-${c.e}`}>
                <span className="lbl clip-when">{fmtDay(c.heard_at.slice(0, 10))} · {fmtWhen(c.heard_at).split(' · ')[1]}</span>
                <ClipPlayer clip={c} />
              </li>
            ))}
          </ul>
        )}
        {hasMore && (
          <button className="chip" style={{ marginTop: 10 }} onClick={loadMore}>
            More clips
          </button>
        )}
      </div>
    </div>
  )
}
