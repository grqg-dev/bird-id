import { useMemo, useState } from 'react'
import type { Sliced } from '../lib/slice'
import type { Summary } from '../lib/types'
import { fmtDay, fmtInt } from '../lib/format'
import { Sprite } from './Sprite'
import { useTheme } from '../theme'

type Sort = 'dex' | 'heard' | 'newest' | 'rarest'

/** Dex № = order of first-ever detection, stable like a real dex. */
export function dexNumbers(summary: Summary): number[] {
  const order = summary.species
    .map((sp, si) => ({ si, first: sp.first }))
    .sort((a, b) => a.first.localeCompare(b.first) || a.si - b.si)
  const no = new Array<number>(summary.species.length)
  order.forEach((e, i) => (no[e.si] = i + 1))
  return no
}

/** W7 · every species as a dex card, sortable, badged. */
export function DexGrid({
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
  const { theme } = useTheme()
  const [sort, setSort] = useState<Sort>('dex')
  const numbers = useMemo(() => dexNumbers(summary), [summary])
  const q = query.trim().toLowerCase()
  const windowDays = new Set(summary.days.slice(sliced.window.lo, sliced.window.hi + 1))

  const entries = summary.species
    .map((sp, si) => ({ sp, si, count: sliced.perSpecies[si] }))
    .filter(
      (e) =>
        !q || e.sp.name.toLowerCase().includes(q) || e.sp.sci.toLowerCase().includes(q),
    )

  entries.sort((a, b) => {
    if (sort === 'heard') return b.count - a.count || numbers[a.si] - numbers[b.si]
    if (sort === 'newest') return b.sp.first.localeCompare(a.sp.first)
    if (sort === 'rarest') {
      const az = a.count === 0 ? Infinity : a.count
      const bz = b.count === 0 ? Infinity : b.count
      return az - bz || numbers[a.si] - numbers[b.si]
    }
    return numbers[a.si] - numbers[b.si]
  })

  const sorts: { key: Sort; label: string }[] = [
    { key: 'dex', label: '№' },
    { key: 'heard', label: 'Most heard' },
    { key: 'newest', label: 'Newest' },
    { key: 'rarest', label: 'Rarest' },
  ]

  return (
    <div className="panel">
      <h2 className="panel-title">
        {theme === 'game' ? 'Birdex' : 'All species'}
        <span className="sub">
          {sliced.speciesCount} of {summary.species.length} heard in this slice
        </span>
      </h2>
      <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
        {sorts.map((s) => (
          <button
            key={s.key}
            className={`chip ${sort === s.key ? 'on' : ''}`}
            onClick={() => setSort(s.key)}
          >
            {s.label}
          </button>
        ))}
      </div>
      <div className="dex-grid">
        {entries.map(({ sp, si, count }) => {
          const isNew = windowDays.has(sp.first.slice(0, 10)) && count > 0
          const night =
            count > 0 && sliced.perSpeciesNight[si] > Math.max(4, count * 0.5)
          const rare = count > 0 && count <= 5
          return (
            <button
              key={si}
              className={`dex-card ${count === 0 ? 'silent' : ''}`}
              onClick={() => onOpen(si)}
            >
              <div className="dex-no lbl">
                №{String(numbers[si]).padStart(3, '0')}
              </div>
              <Sprite sp={sp} size={84} />
              <div className="dex-name">{sp.name}</div>
              <div className="num dex-count">
                {count > 0 ? `×${fmtInt(count)}` : `last ${fmtDay(sp.last.slice(0, 10))}`}
              </div>
              <div className="dex-badges">
                {isNew && <span className="badge new">★ new</span>}
                {night && <span className="badge night">◐ night</span>}
                {rare && <span className="badge">♦ rare</span>}
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
