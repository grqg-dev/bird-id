import type { Sliced } from '../lib/slice'
import type { Slice, Summary } from '../lib/types'
import { fmtClock, fmtHour, fmtInt } from '../lib/format'
import { useTip } from './Tip'

/** W3 · 24-hour activity histogram with sunrise/sunset markers. */
export function DayRhythm({
  summary,
  sliced,
  slice,
  setSlice,
}: {
  summary: Summary
  sliced: Sliced
  slice: Slice
  setSlice: (s: Slice) => void
}) {
  const tip = useTip()
  const counts = sliced.perHourNav
  const max = Math.max(1, ...counts)
  const sun = summary.sun[sliced.window.hi] ?? { rise: 6, set: 19.5 }

  const W = 480
  const H = 120
  const pad = 4
  const bw = (W - pad * 2) / 24

  return (
    <div className="panel">
      <h2 className="panel-title">
        Day rhythm
        <span className="sub">
          ☀ {fmtClock(sun.rise)} · {fmtClock(sun.set)} · click an hour
        </span>
      </h2>
      <svg
        viewBox={`0 0 ${W} ${H + 18}`}
        width="100%"
        role="img"
        aria-label="Detections by hour of day"
      >
        {/* night shading */}
        <rect x={pad} y={0} width={(sun.rise / 24) * (W - pad * 2)} height={H} fill="var(--well)" />
        <rect
          x={pad + (sun.set / 24) * (W - pad * 2)}
          y={0}
          width={(1 - sun.set / 24) * (W - pad * 2)}
          height={H}
          fill="var(--well)"
        />
        {counts.map((c, h) => {
          const bh = Math.max(c > 0 ? 3 : 0, (c / max) * (H - 8))
          const on = slice.hour === h
          return (
            <rect
              key={h}
              x={pad + h * bw + 1.5}
              y={H - bh}
              width={bw - 3}
              height={bh}
              rx={2}
              fill={on ? 'var(--accent)' : 'var(--heat3)'}
              opacity={slice.hour !== null && !on ? 0.35 : 1}
              style={{ cursor: 'pointer' }}
              onMouseMove={(e) => tip.show(e, `${fmtHour(h)} · ${fmtInt(c)} calls`)}
              onMouseLeave={tip.hide}
              onClick={() =>
                setSlice({ ...slice, hour: slice.hour === h ? null : h, band: 'all' })
              }
            />
          )
        })}
        {/* sunrise / sunset ticks */}
        {[sun.rise, sun.set].map((t, i) => (
          <line
            key={i}
            x1={pad + (t / 24) * (W - pad * 2)}
            x2={pad + (t / 24) * (W - pad * 2)}
            y1={0}
            y2={H}
            stroke="var(--line-strong)"
            strokeDasharray="3 3"
          />
        ))}
        <line x1={pad} x2={W - pad} y1={H} y2={H} stroke="var(--line-strong)" />
        {[0, 6, 12, 18].map((h) => (
          <text
            key={h}
            x={pad + h * bw + bw / 2}
            y={H + 13}
            textAnchor="middle"
            fontSize={9}
            fontFamily="var(--font-label)"
            fill="var(--muted)"
          >
            {fmtHour(h)}
          </text>
        ))}
      </svg>
    </div>
  )
}
