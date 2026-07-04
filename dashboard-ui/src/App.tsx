import { useEffect, useMemo, useState } from 'react'
import type { Slice, Summary } from './lib/types'
import { DEFAULT_SLICE } from './lib/types'
import { computeSlice } from './lib/slice'
import { fmtDay } from './lib/format'
import { useTheme } from './theme'
import { TipProvider } from './components/Tip'
import { Slicer } from './components/Slicer'
import { HeadlineStrip } from './components/HeadlineStrip'
import { FieldLog } from './components/FieldLog'
import { DayRhythm } from './components/DayRhythm'
import { Punchcard } from './components/Punchcard'
import { Party } from './components/Party'
import { Leaderboard } from './components/Leaderboard'
import { DexGrid } from './components/DexGrid'
import { DexEntry } from './components/DexEntry'
import { Discoveries } from './components/Discoveries'
import { RareNight } from './components/RareNight'
import { FieldNote } from './components/FieldNote'

export default function App() {
  const { theme, toggle } = useTheme()
  const [summary, setSummary] = useState<Summary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [slice, setSlice] = useState<Slice>(DEFAULT_SLICE)
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<number | null>(null)

  useEffect(() => {
    fetch('/api/dash/summary')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(setSummary)
      .catch((e) => setError(String(e)))
  }, [])

  const sliced = useMemo(
    () => (summary ? computeSlice(summary, slice) : null),
    [summary, slice],
  )

  if (error) {
    return (
      <div className="wrap">
        <div className="panel" style={{ marginTop: 60, textAlign: 'center' }}>
          <p>couldn’t load the data ({error})</p>
          <p className="lbl">is the bird-id dashboard running?</p>
        </div>
      </div>
    )
  }
  if (!summary || !sliced) {
    return (
      <div className="wrap">
        <div className="loading lbl">listening…</div>
      </div>
    )
  }

  return (
    <TipProvider>
      <div className="wrap">
        <header className="masthead">
          <div>
            <h1 className="site-title">
              {theme === 'game' ? 'BIRDEX' : 'Backyard Birds'}
            </h1>
            <div className="lbl">
              {summary.meta.location} · {fmtDay(summary.days[0])} –{' '}
              {fmtDay(summary.days[summary.days.length - 1])}
            </div>
            <FieldNote summary={summary} sliced={sliced} />
          </div>
          <div className="masthead-right">
            <button className="chip theme-toggle" onClick={toggle}>
              {theme === 'game' ? 'Clean mode' : 'Game mode'}
            </button>
            <a className="lbl" href="/">
              old dex ↗
            </a>
          </div>
        </header>

        <Slicer
          summary={summary}
          slice={slice}
          setSlice={setSlice}
          query={query}
          setQuery={setQuery}
        />

        <HeadlineStrip summary={summary} sliced={sliced} />

        <div className="grid-2" style={{ marginTop: 16 }}>
          <FieldLog summary={summary} sliced={sliced} slice={slice} setSlice={setSlice} />
          <DayRhythm summary={summary} sliced={sliced} slice={slice} setSlice={setSlice} />
        </div>

        <div style={{ marginTop: 16 }}>
          <Party summary={summary} sliced={sliced} onOpen={setOpen} />
        </div>

        <div className="grid-2" style={{ marginTop: 16 }}>
          <Leaderboard summary={summary} sliced={sliced} query={query} onOpen={setOpen} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <Discoveries summary={summary} onOpen={setOpen} />
            <Punchcard summary={summary} sliced={sliced} slice={slice} setSlice={setSlice} />
          </div>
        </div>

        <div style={{ marginTop: 16 }}>
          <RareNight summary={summary} sliced={sliced} onOpen={setOpen} />
        </div>

        <div style={{ marginTop: 16 }}>
          <DexGrid summary={summary} sliced={sliced} query={query} onOpen={setOpen} />
        </div>

        <footer className="lbl" style={{ marginTop: 26, textAlign: 'center' }}>
          {summary.species.length} species on record · data refreshed{' '}
          {summary.meta.generated_at.replace('T', ' ')}
        </footer>
      </div>

      {open !== null && (
        <DexEntry
          summary={summary}
          si={open}
          slice={slice}
          onClose={() => setOpen(null)}
          onFocus={(si) => setSlice({ ...slice, species: si })}
        />
      )}
    </TipProvider>
  )
}
