import type { Band, RangeKey, Slice, Summary } from '../lib/types'
import { fmtDay } from '../lib/format'

const RANGES: { key: RangeKey; label: string }[] = [
  { key: '7d', label: '7d' },
  { key: '14d', label: '14d' },
  { key: '30d', label: '30d' },
  { key: 'all', label: 'All' },
]

const BANDS: { key: Band; label: string }[] = [
  { key: 'all', label: 'All hours' },
  { key: 'dawn', label: 'Dawn' },
  { key: 'day', label: 'Day' },
  { key: 'dusk', label: 'Dusk' },
  { key: 'night', label: 'Night' },
]

const CONFS: { key: 0 | 1 | 2 | 3; label: string }[] = [
  { key: 0, label: 'All conf' },
  { key: 1, label: '≥.5' },
  { key: 2, label: '≥.7' },
  { key: 3, label: '≥.9' },
]

interface Props {
  summary: Summary
  slice: Slice
  setSlice: (s: Slice) => void
  query: string
  setQuery: (q: string) => void
}

export function Slicer({ summary, slice, setSlice, query, setQuery }: Props) {
  const focused =
    slice.day !== null ||
    slice.hour !== null ||
    slice.band !== 'all' ||
    slice.confMin !== 0 ||
    slice.species !== null ||
    query !== ''

  return (
    <div className="slicer">
      <div className="slicer-group" role="group" aria-label="Date range">
        {RANGES.map((r) => (
          <button
            key={r.key}
            className={`chip ${slice.range === r.key && slice.day === null ? 'on' : ''}`}
            onClick={() => setSlice({ ...slice, range: r.key, day: null })}
          >
            {r.label}
          </button>
        ))}
        {slice.day !== null && (
          <button
            className="chip on"
            title="Clear day focus"
            onClick={() => setSlice({ ...slice, day: null })}
          >
            {fmtDay(summary.days[slice.day])} ✕
          </button>
        )}
      </div>

      <div className="slicer-group" role="group" aria-label="Hours">
        {BANDS.map((b) => (
          <button
            key={b.key}
            className={`chip ${slice.band === b.key && slice.hour === null ? 'on' : ''}`}
            onClick={() => setSlice({ ...slice, band: b.key, hour: null })}
          >
            {b.label}
          </button>
        ))}
        {slice.hour !== null && (
          <button
            className="chip on"
            title="Clear hour focus"
            onClick={() => setSlice({ ...slice, hour: null })}
          >
            {slice.hour}:00 ✕
          </button>
        )}
      </div>

      <div className="slicer-group" role="group" aria-label="Confidence">
        {CONFS.map((c) => (
          <button
            key={c.key}
            className={`chip ${slice.confMin === c.key ? 'on' : ''}`}
            onClick={() => setSlice({ ...slice, confMin: c.key })}
          >
            {c.label}
          </button>
        ))}
      </div>

      <div className="slicer-group" style={{ marginLeft: 'auto' }}>
        {slice.species !== null && (
          <button
            className="chip on"
            title="Clear species focus"
            onClick={() => setSlice({ ...slice, species: null })}
          >
            {summary.species[slice.species].name} ✕
          </button>
        )}
        <input
          className="search"
          type="search"
          placeholder="find a bird…"
          value={query}
          aria-label="Search species"
          onChange={(e) => setQuery(e.target.value)}
        />
        {focused && (
          <button
            className="chip"
            onClick={() => {
              setQuery('')
              setSlice({ range: slice.range, day: null, band: 'all', hour: null, confMin: 0, species: null })
            }}
          >
            Reset
          </button>
        )}
      </div>
    </div>
  )
}
