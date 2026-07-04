import { useState } from 'react'
import type { Sliced } from '../lib/slice'
import type { Summary } from '../lib/types'
import { ranked } from '../lib/slice'
import { fmtInt } from '../lib/format'
import { useTip } from './Tip'

/** W6 · every species in the slice, ranked, with trend vs the prior window. */
export function Leaderboard({
  summary,
  sliced,
  query,
  onOpen,
}: {
  summary: Summary
  sliced: Sliced
  query: string
  onOpen: (si: number) => void
}) {
  const tip = useTip()
  const [expanded, setExpanded] = useState(false)
  const q = query.trim().toLowerCase()
  const order = ranked(sliced).filter(
    (si) =>
      !q ||
      summary.species[si].name.toLowerCase().includes(q) ||
      summary.species[si].sci.toLowerCase().includes(q),
  )
  const hasPrior = sliced.window.priorHi >= sliced.window.priorLo
  const shown = expanded ? order : order.slice(0, 15)
  const max = Math.max(1, ...order.map((si) => sliced.perSpecies[si]))

  return (
    <div className="panel">
      <h2 className="panel-title">
        Leaderboard
        <span className="sub">
          {order.length} species{hasPrior ? ' · vs prior window' : ''}
        </span>
      </h2>
      <ol className="board">
        {shown.map((si, rank) => {
          const sp = summary.species[si]
          const count = sliced.perSpecies[si]
          const prior = sliced.perSpeciesPrior[si]
          let trend: string | null = null
          let dir: 'up' | 'down' | null = null
          if (hasPrior && prior > 0) {
            const pct = ((count - prior) / prior) * 100
            if (Math.abs(pct) >= 5) {
              dir = pct > 0 ? 'up' : 'down'
              trend = `${pct > 0 ? '▲' : '▼'}${Math.abs(pct) >= 200 ? '2x+' : Math.round(Math.abs(pct)) + '%'}`
            }
          } else if (hasPrior && count > 0) {
            dir = 'up'
            trend = '★'
          }
          return (
            <li key={si}>
              <button
                className="board-row"
                onClick={() => onOpen(si)}
                onMouseMove={(e) =>
                  trend === '★'
                    ? tip.show(e, 'not heard in the prior window')
                    : tip.hide()
                }
                onMouseLeave={tip.hide}
              >
                <span className="num board-rank">{rank + 1}</span>
                <span className="board-name">{sp.name}</span>
                <span className="board-bar">
                  <span
                    className="board-fill"
                    style={{ width: `${Math.max(1.5, (count / max) * 100)}%` }}
                  />
                </span>
                <span className="num board-count">{fmtInt(count)}</span>
                <span
                  className={`num board-trend ${dir ?? ''}`}
                  aria-label={trend ?? undefined}
                >
                  {trend ?? ''}
                </span>
              </button>
            </li>
          )
        })}
      </ol>
      {order.length > 15 && (
        <button className="chip" style={{ marginTop: 8 }} onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Show top 15' : `Show all ${order.length}`}
        </button>
      )}
    </div>
  )
}
