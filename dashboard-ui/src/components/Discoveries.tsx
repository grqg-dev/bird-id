import { useMemo } from 'react'
import type { Summary } from '../lib/types'
import { fmtDay } from '../lib/format'
import { useTip } from './Tip'

/** W9 · when each species was first heard: cumulative curve + arrival dots. */
export function Discoveries({
  summary,
  onOpen,
}: {
  summary: Summary
  onOpen: (si: number) => void
}) {
  const tip = useTip()
  const { days } = summary

  const { byDay, cumulative } = useMemo(() => {
    const byDay = new Map<number, number[]>()
    summary.species.forEach((sp, si) => {
      const di = days.indexOf(sp.first.slice(0, 10))
      if (di < 0) return
      const list = byDay.get(di) ?? []
      list.push(si)
      byDay.set(di, list)
    })
    const cumulative: number[] = []
    let acc = 0
    for (let di = 0; di < days.length; di++) {
      acc += byDay.get(di)?.length ?? 0
      cumulative.push(acc)
    }
    return { byDay, cumulative }
  }, [summary, days])

  const W = 480
  const H = 110
  const padL = 6
  const padR = 6
  const total = cumulative[cumulative.length - 1]
  const x = (di: number) => padL + (di / (days.length - 1)) * (W - padL - padR)
  const y = (c: number) => 8 + (1 - c / total) * (H - 30)

  const stepPath = cumulative
    .map((c, di) => `${di === 0 ? 'M' : 'L'}${x(di).toFixed(1)},${y(c).toFixed(1)}`)
    .join(' ')

  // Arrivals after day one, newest last (day one is the starter pack).
  const arrivals = [...byDay.entries()].filter(([di]) => di > 0).sort((a, b) => a[0] - b[0])

  return (
    <div className="panel">
      <h2 className="panel-title">
        New encounters
        <span className="sub">
          {byDay.get(0)?.length ?? 0} species on day one → {total} total
        </span>
      </h2>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Cumulative species discovered">
        <path d={stepPath} fill="none" stroke="var(--heat3)" strokeWidth={2} />
        {[...byDay.entries()].map(([di, list]) => (
          <circle
            key={di}
            cx={x(di)}
            cy={y(cumulative[di])}
            r={di === 0 ? 5 : 3.5}
            fill={di === 0 ? 'var(--heat4)' : 'var(--accent)'}
            style={{ cursor: 'pointer' }}
            onMouseMove={(e) =>
              tip.show(
                e,
                `${fmtDay(days[di])} · ${
                  di === 0
                    ? `${list.length} species (starter pack)`
                    : list.map((si) => summary.species[si].name).join(', ')
                }`,
              )
            }
            onMouseLeave={tip.hide}
            onClick={() => list.length === 1 && onOpen(list[0])}
          />
        ))}
        <text x={padL} y={H - 2} fontSize={9} fontFamily="var(--font-label)" fill="var(--muted)">
          {fmtDay(days[0])}
        </text>
        <text x={W - padR} y={H - 2} textAnchor="end" fontSize={9} fontFamily="var(--font-label)" fill="var(--muted)">
          {fmtDay(days[days.length - 1])}
        </text>
      </svg>
      <div className="arrivals">
        {arrivals.slice(-8).map(([di, list]) =>
          list.map((si) => (
            <button key={si} className="chip" onClick={() => onOpen(si)}>
              {fmtDay(days[di])} · {summary.species[si].name}
            </button>
          )),
        )}
      </div>
    </div>
  )
}
