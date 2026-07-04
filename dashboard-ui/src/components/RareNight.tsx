import type { Sliced } from '../lib/slice'
import type { Summary } from '../lib/types'
import { ranked } from '../lib/slice'
import { fmtDay, fmtInt } from '../lib/format'

/** W10 · one-day wonders and the night shift, side by side. */
export function RareNight({
  summary,
  sliced,
  onOpen,
}: {
  summary: Summary
  sliced: Sliced
  onOpen: (si: number) => void
}) {
  const order = ranked(sliced)

  const wonders = order
    .filter((si) => sliced.perSpeciesDays[si] === 1 && sliced.perSpecies[si] <= 12)
    .sort((a, b) => sliced.perSpecies[a] - sliced.perSpecies[b])
    .slice(0, 10)

  const night = order
    .filter((si) => sliced.perSpeciesNight[si] > 0)
    .sort((a, b) => sliced.perSpeciesNight[b] - sliced.perSpeciesNight[a])
    .slice(0, 8)

  return (
    <div className="grid-2">
      <div className="panel">
        <h2 className="panel-title">
          One-day wonders
          <span className="sub">heard a single day in this slice</span>
        </h2>
        {wonders.length === 0 ? (
          <p className="lbl">none in this slice</p>
        ) : (
          <ul className="plain-list">
            {wonders.map((si) => {
              const sp = summary.species[si]
              return (
                <li key={si}>
                  <button className="row-btn" onClick={() => onOpen(si)}>
                    <span>♦ {sp.name}</span>
                    <span className="num faint">
                      ×{sliced.perSpecies[si]} · {fmtDay(sp.first.slice(0, 10))}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>

      <div className="panel">
        <h2 className="panel-title">
          Night shift
          <span className="sub">calls between dusk and dawn</span>
        </h2>
        {night.length === 0 ? (
          <p className="lbl">the yard sleeps</p>
        ) : (
          <ul className="plain-list">
            {night.map((si) => {
              const sp = summary.species[si]
              const n = sliced.perSpeciesNight[si]
              const share = n / Math.max(1, sliced.perSpecies[si])
              const trueNight = share >= 0.3
              return (
                <li key={si}>
                  <button className="row-btn" onClick={() => onOpen(si)}>
                    <span>
                      {trueNight ? '◐' : '·'} {sp.name}
                    </span>
                    <span className="num faint">
                      ×{fmtInt(n)}
                      {!trueNight && ' (dawn edge)'}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
