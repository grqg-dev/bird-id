import type { Sliced } from '../lib/slice'
import type { Slice, Summary } from '../lib/types'
import { fmtDay, fmtHour, fmtInt } from '../lib/format'
import { fmtRecorded, type Coverage } from '../lib/health'
import { useTip } from './Tip'

/** W4 · window days × 24 hours heat grid — the month's texture in one glance. */
export function Punchcard({
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
  const { lo, hi } = sliced.window
  // Punchcard always shows the range window even when a single day is focused.
  const showLo = slice.day !== null ? Math.max(0, slice.day - 15) : lo
  const showHi = slice.day !== null ? Math.min(summary.days.length - 1, slice.day + 15) : hi
  const rows = []
  let max = 1
  for (let di = showLo; di <= showHi; di++) {
    for (let h = 0; h < 24; h++) max = Math.max(max, sliced.punchcard[di][h])
  }

  const cell = 15
  const gap = 2
  const left = 44
  const top = 16
  const W = left + 24 * (cell + gap)
  const H = top + (showHi - showLo + 1) * (cell + gap)
  const excluded = new Set(summary.meta.excluded_days)

  for (let di = showLo; di <= showHi; di++) {
    const isExcluded = excluded.has(summary.days[di])
    const y = top + (di - showLo) * (cell + gap)
    const cells = []
    for (let h = 0; h < 24; h++) {
      const v = sliced.punchcard[di][h]
      const t = v / max
      const fill =
        v <= 0
          ? 'var(--heat0)'
          : t < 0.15
            ? 'var(--heat1)'
            : t < 0.4
              ? 'var(--heat2)'
              : t < 0.75
                ? 'var(--heat3)'
                : 'var(--heat4)'
      const focused =
        (slice.day === null || slice.day === di) && (slice.hour === null || slice.hour === h)
      const lowCov = !isExcluded && cov !== null && cov.hourLow(di, h)
      const rec = cov?.recorded(di, h) ?? null
      cells.push(
        <g key={h}>
        <rect
          x={left + h * (cell + gap)}
          y={y}
          width={cell}
          height={cell}
          rx={2}
          fill={isExcluded ? 'transparent' : fill}
          stroke={isExcluded ? 'var(--line)' : 'none'}
          strokeDasharray={isExcluded ? '2 2' : undefined}
          opacity={focused ? 1 : 0.3}
          style={{ cursor: isExcluded ? 'default' : 'pointer' }}
          onMouseMove={(e) =>
            tip.show(
              e,
              isExcluded
                ? `${fmtDay(summary.days[di])} · excluded (sensor fault)`
                : `${fmtDay(summary.days[di])} · ${fmtHour(h)} · ${fmtInt(v)} calls${
                    lowCov && rec !== null ? ` · mic off: ${fmtRecorded(rec / 3600, (cov?.expected(di, h) ?? 3600) / 3600)}` : ''
                  }`,
            )
          }
          onMouseLeave={tip.hide}
          onClick={() => {
            if (isExcluded) return
            const same = slice.day === di && slice.hour === h
            setSlice({
              ...slice,
              day: same ? null : di,
              hour: same ? null : h,
              band: 'all',
            })
          }}
        />
        {lowCov && (
          <rect
            x={left + h * (cell + gap)}
            y={y}
            width={cell}
            height={cell}
            rx={2}
            fill="url(#hatch)"
            stroke="var(--down)"
            strokeWidth={0.75}
            opacity={focused ? 1 : 0.3}
            pointerEvents="none"
          />
        )}
        </g>,
      )
    }
    rows.push(
      <g key={di}>
        <text
          x={left - 6}
          y={y + cell - 3}
          textAnchor="end"
          fontSize={8.5}
          fontFamily="var(--font-label)"
          fill="var(--muted)"
        >
          {fmtDay(summary.days[di])}
        </text>
        {cells}
      </g>,
    )
  }

  return (
    <div className="panel">
      <h2 className="panel-title">
        Punchcard
        <span className="sub">every day × every hour · lone night cells are owls{cov ? ' · hatched = mic off' : ''}</span>
      </h2>
      <div style={{ overflowX: 'auto' }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ minWidth: 560 }} role="img" aria-label="Detections per day and hour">
          {[0, 6, 12, 18].map((h) => (
            <text
              key={h}
              x={left + h * (cell + gap) + cell / 2}
              y={10}
              textAnchor="middle"
              fontSize={8.5}
              fontFamily="var(--font-label)"
              fill="var(--muted)"
            >
              {fmtHour(h)}
            </text>
          ))}
          <defs>
            <pattern id="hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="4" stroke="#fff" strokeOpacity="0.7" strokeWidth="1.8" />
            </pattern>
          </defs>
          {rows}
        </svg>
      </div>
    </div>
  )
}
