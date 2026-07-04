import type { Sliced } from '../lib/slice'
import type { Summary } from '../lib/types'
import { fmtDay, fmtHour, fmtInt } from '../lib/format'

function Stat({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="stat">
      <div className="stat-value num">{value}</div>
      <div className="lbl">{label}</div>
      {detail && <div className="stat-detail">{detail}</div>}
    </div>
  )
}

export function HeadlineStrip({ summary, sliced }: { summary: Summary; sliced: Sliced }) {
  const winLen = sliced.window.hi - sliced.window.lo + 1
  const newCount = sliced.discoveredInWindow.filter(
    (si) => summary.species[si].first.slice(0, 10) !== summary.days[0],
  ).length

  return (
    <div className="panel headline">
      <Stat label="species" value={String(sliced.speciesCount)} />
      <Stat label="detections" value={fmtInt(sliced.total)} />
      <Stat label="days" value={String(winLen)} />
      <Stat
        label="busiest day"
        value={sliced.busiestDay ? fmtDay(summary.days[sliced.busiestDay.di]) : '—'}
        detail={sliced.busiestDay ? `${fmtInt(sliced.busiestDay.count)} calls` : undefined}
      />
      <Stat
        label="peak hour"
        value={sliced.busiestHour ? fmtHour(sliced.busiestHour.hour) : '—'}
        detail={sliced.busiestHour ? `${fmtInt(sliced.busiestHour.count)} calls` : undefined}
      />
      <Stat label="new arrivals" value={newCount > 0 ? `+${newCount}` : '0'} detail="first heard in window" />
    </div>
  )
}
