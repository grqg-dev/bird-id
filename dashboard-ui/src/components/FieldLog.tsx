import { rangeWindow, type Sliced } from '../lib/slice'
import type { Slice, Summary } from '../lib/types'
import { fmtDay, fmtInt } from '../lib/format'
import { fmtRecorded, type Coverage } from '../lib/health'
import { useTip } from './Tip'

function heatVar(v: number, max: number): string {
  if (v <= 0) return 'var(--heat0)'
  const t = max > 0 ? v / max : 0
  if (t < 0.25) return 'var(--heat1)'
  if (t < 0.5) return 'var(--heat2)'
  if (t < 0.8) return 'var(--heat3)'
  return 'var(--heat4)'
}

/** W2 · calendar heatmap of the recorded span; click a day to focus it. */
export function FieldLog({
  summary,
  sliced,
  slice,
  setSlice,
  cov,
}: {
  summary: Summary
  sliced: Sliced
  slice: Slice
  setSlice: (s: Slice) => void
  cov: Coverage | null
}) {
  const tip = useTip()
  const days = summary.days
  const excluded = new Set(summary.meta.excluded_days)
  // Only the selected range (7d/14d/30d/all); a single-day focus keeps the range visible.
  const rw = rangeWindow(summary, slice)
  const shown = days.map((_, i) => i).filter((i) => i >= rw.lo && i <= rw.hi)
  const max = Math.max(1, ...shown.map((i) => sliced.perDayNav[i]))

  // Lay out as ISO weeks, Monday-first.
  const firstDow = (new Date(days[rw.lo] + 'T12:00:00').getDay() + 6) % 7
  const cells: (number | null)[] = [
    ...Array.from({ length: firstDow }, () => null),
    ...shown,
  ]
  while (cells.length % 7 !== 0) cells.push(null)
  const weeks: (number | null)[][] = []
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7))

  return (
    <div className="panel">
      <h2 className="panel-title">
        Field log
        <span className="sub">
          {fmtDay(days[rw.lo])} – {fmtDay(days[rw.hi])} · click a day
          {cov ? ' · hatched = mic off' : ''}
        </span>
      </h2>
      <div className="cal">
        <div className="cal-head">
          {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((d, i) => (
            <span key={i} className="lbl">
              {d}
            </span>
          ))}
        </div>
        {weeks.map((week, wi) => (
          <div key={wi} className="cal-row">
            {week.map((di, ci) => {
              if (di === null) return <span key={ci} className="cal-cell blank" />
              const day = days[di]
              const v = sliced.perDayNav[di]
              const isExcluded = excluded.has(day)
              const selected = slice.day === di
              const lowCov = !isExcluded && cov !== null && cov.dayLow(di)
              const dh = cov?.dayHours(di) ?? null
              return (
                <button
                  key={ci}
                  className={`cal-cell ${selected ? 'sel' : ''} ${lowCov ? 'low-cov' : ''}`}
                  style={{ background: isExcluded ? 'transparent' : heatVar(v, max) }}
                  aria-label={`${fmtDay(day)}: ${fmtInt(v)} detections${lowCov ? ', mic recorded under half the day' : ''}`}
                  onMouseMove={(e) =>
                    tip.show(
                      e,
                      isExcluded
                        ? `${fmtDay(day)} · excluded (sensor fault)`
                        : `${fmtDay(day)} · ${fmtInt(v)} calls${
                            lowCov && dh ? ` · mic off: ${fmtRecorded(dh.recorded, dh.expected)}` : ''
                          }`,
                    )
                  }
                  onMouseLeave={tip.hide}
                  onClick={() =>
                    !isExcluded && setSlice({ ...slice, day: selected ? null : di })
                  }
                >
                  {isExcluded ? '×' : new Date(day + 'T12:00:00').getDate()}
                </button>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}
