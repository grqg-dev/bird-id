import { useEffect, useMemo } from 'react'
import type { Slice, Summary } from '../lib/types'
import { fmtHour, fmtInt, fmtWhen } from '../lib/format'
import { Sprite } from './Sprite'
import { Sparkline } from './Sparkline'
import { dexNumbers } from './DexGrid'
import { useTheme } from '../theme'
import { useTip } from './Tip'

/** W8 · drawer with the full dex entry for one species. */
export function DexEntry({
  summary,
  si,
  slice,
  onClose,
  onFocus,
}: {
  summary: Summary
  si: number
  slice: Slice
  onClose: () => void
  onFocus: (si: number) => void
}) {
  const { theme } = useTheme()
  const tip = useTip()
  const sp = summary.species[si]
  const numbers = useMemo(() => dexNumbers(summary), [summary])

  // All-time per-hour + per-day profile for this species (conf floor respected).
  const { hourly, daily } = useMemo(() => {
    const hourly = new Array<number>(24).fill(0)
    const daily = new Array<number>(summary.days.length).fill(0)
    for (const [di, h, csi, cb, n] of summary.cube) {
      if (csi !== si || cb < slice.confMin) continue
      hourly[h] += n
      daily[di] += n
    }
    return { hourly, daily }
  }, [summary, si, slice.confMin])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const maxH = Math.max(1, ...hourly)
  const info = sp.info

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={`${sp.name} dex entry`}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div className="lbl">№{String(numbers[si]).padStart(3, '0')}</div>
          <button className="chip" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div style={{ display: 'flex', gap: 16, alignItems: 'center', margin: '10px 0 4px' }}>
          <Sprite sp={sp} size={110} />
          <div>
            <h2 className="dex-entry-name">{sp.name}</h2>
            <div className="dex-entry-sci">{sp.sci}</div>
            {info?.group && <div className="lbl" style={{ marginTop: 4 }}>{info.group}</div>}
          </div>
        </div>

        <div className="entry-stats num">
          <span>×{fmtInt(sp.total)} all-time</span>
          <span>peak {theme === 'game' ? `Lv.${Math.round(sp.peak * 100)}` : sp.peak.toFixed(2)}</span>
        </div>
        <div className="entry-stats lbl">
          <span>first {fmtWhen(sp.first)}</span>
          <span>last {fmtWhen(sp.last)}</span>
        </div>

        {sp.clip ? (
          <div style={{ margin: '12px 0' }}>
            <div className="lbl" style={{ marginBottom: 4 }}>
              {theme === 'game' ? 'Cry' : 'Best recording'} · conf {sp.clip.conf.toFixed(2)}
            </div>
            <audio
              controls
              preload="none"
              style={{ width: '100%', height: 34 }}
              src={`/audio/${sp.clip.seg}?start=${sp.clip.s}&end=${sp.clip.e}`}
            />
          </div>
        ) : (
          <div className="lbl" style={{ margin: '12px 0' }}>
            no recording kept on this machine
          </div>
        )}

        {info?.sound && <p className="entry-blurb">“{info.sound}”</p>}
        {info?.note && <p className="entry-blurb">{info.note}</p>}
        {(info?.habitat || info?.season) && (
          <p className="entry-blurb faint">
            {[info.habitat, info.season].filter(Boolean).join(' · ')}
          </p>
        )}

        <div className="lbl" style={{ margin: '14px 0 4px' }}>when it calls (all-time)</div>
        <svg viewBox="0 0 240 44" width="100%" role="img" aria-label="Calls by hour of day">
          {hourly.map((c, h) => {
            const bh = Math.max(c > 0 ? 2 : 0, (c / maxH) * 36)
            return (
              <rect
                key={h}
                x={h * 10 + 1}
                y={40 - bh}
                width={8}
                height={bh}
                rx={1.5}
                fill="var(--heat3)"
                onMouseMove={(e) => tip.show(e, `${fmtHour(h)} · ${fmtInt(c)} calls`)}
                onMouseLeave={tip.hide}
              />
            )
          })}
          <line x1={0} x2={240} y1={40.5} y2={40.5} stroke="var(--line-strong)" />
        </svg>

        <div className="lbl" style={{ margin: '12px 0 4px' }}>day by day</div>
        <Sparkline data={daily} width={380} height={34} />

        <div style={{ display: 'flex', gap: 8, marginTop: 18, flexWrap: 'wrap' }}>
          <button
            className="chip on"
            onClick={() => {
              onFocus(si)
              onClose()
            }}
          >
            Slice dashboard to this bird
          </button>
          <a className="chip" href={`/bird/${sp.slug}`}>
            All clips ↗
          </a>
        </div>
      </aside>
    </>
  )
}
