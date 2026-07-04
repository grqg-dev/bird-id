import type { Sliced } from '../lib/slice'
import type { Summary } from '../lib/types'
import { ranked } from '../lib/slice'
import { fmtInt } from '../lib/format'
import { Sprite } from './Sprite'
import { Sparkline } from './Sparkline'
import { useTheme } from '../theme'

/** W5 · the six most-heard species in the slice. Game skin calls it the party. */
export function Party({
  summary,
  sliced,
  onOpen,
}: {
  summary: Summary
  sliced: Sliced
  onOpen: (si: number) => void
}) {
  const { theme } = useTheme()
  const top = ranked(sliced).slice(0, 6)
  const daily = sliced.speciesDaily()
  const { lo, hi } = sliced.window

  if (top.length === 0) return null
  return (
    <div className="panel">
      <h2 className="panel-title">
        {theme === 'game' ? 'Your party' : 'The regulars'}
        <span className="sub">most heard in this slice</span>
      </h2>
      <div className="party">
        {top.map((si) => {
          const sp = summary.species[si]
          const series = (daily.get(si) ?? []).slice(lo, hi + 1)
          return (
            <button key={si} className="party-slot" onClick={() => onOpen(si)}>
              <Sprite sp={sp} size={72} />
              <div className="party-name">{sp.name}</div>
              <div className="num party-count">×{fmtInt(sliced.perSpecies[si])}</div>
              {theme === 'game' && (
                <div className="lbl">Lv.{Math.round(sp.peak * 100)}</div>
              )}
              {series.length > 1 && <Sparkline data={series} width={104} height={20} />}
            </button>
          )
        })}
      </div>
    </div>
  )
}
