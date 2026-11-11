import { useEffect, useMemo, useState } from 'react'
import type { Health, Slice, Summary } from './lib/types'
import { DEFAULT_SLICE } from './lib/types'
import { computeSlice } from './lib/slice'
import { buildCoverage } from './lib/health'
import { fmtDay } from './lib/format'
import { useTheme } from './theme'
import { TipProvider } from './components/Tip'
import { UnlikelyToggle } from './components/UnlikelyToggle'
import { HealthBanner } from './components/HealthBanner'
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
import { SureCalls } from './components/SureCalls'

export default function App() {
  const path = window.location.pathname.replace(/\/+$/, '')
  return path === '/dash/sure' ? <SureCalls /> : <Dashboard />
}

function Dashboard() {
  const { theme, toggle } = useTheme()
  const [summary, setSummary] = useState<Summary | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [slice, setSlice] = useState<Slice>(DEFAULT_SLICE)
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const [showUnlikely, setShowUnlikely] = useState<boolean>(() => {
    try {
      return localStorage.getItem('birdid-show-unlikely') === '1'
    } catch {
      return false
    }
  })

  useEffect(() => {
    fetch(showUnlikely ? '/api/dash/summary?unlikely=1' : '/api/dash/summary')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(setSummary)
      .catch((e) => setError(String(e)))
  }, [showUnlikely])

  const toggleUnlikely = (v: boolean) => {
    try {
      localStorage.setItem('birdid-show-unlikely', v ? '1' : '0')
    } catch {
      // storage unavailable: the toggle still works for this page view
    }
    // species indexes change with the payload, so drop index-based focus
    setOpen(null)
    setSlice((s) => ({ ...s, species: null }))
    setShowUnlikely(v)
  }

  // Capture health is advisory: if it fails to load the dashboard still works.
  useEffect(() => {
    fetch('/api/dash/health')
      .then((r) => (r.ok ? r.json() : null))
      .then(setHealth)
      .catch(() => setHealth(null))
  }, [])

  const cov = useMemo(
    () => (summary ? buildCoverage(summary, health) : null),
    [summary, health],
  )
  const sliced = useMemo(
    () => (summary ? computeSlice(summary, slice, cov) : null),
    [summary, slice, cov],
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
        <HealthBanner health={health} />

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
            <a className="lbl" href="/dash/sure">
              sure calls ↗
            </a>
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

        <UnlikelyToggle
          total={summary.meta.unlikely_total}
          hidden={summary.meta.unlikely_hidden}
          show={showUnlikely}
          setShow={toggleUnlikely}
        />

        <HeadlineStrip summary={summary} sliced={sliced} />

        <div className="grid-2" style={{ marginTop: 16 }}>
          <FieldLog summary={summary} sliced={sliced} slice={slice} setSlice={setSlice} cov={cov} />
          <DayRhythm summary={summary} sliced={sliced} slice={slice} setSlice={setSlice} />
        </div>

        <div style={{ marginTop: 16 }}>
          <Party summary={summary} sliced={sliced} onOpen={setOpen} />
        </div>

        <div className="grid-2" style={{ marginTop: 16 }}>
          <Leaderboard summary={summary} sliced={sliced} query={query} onOpen={setOpen} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <Discoveries summary={summary} onOpen={setOpen} />
            <Punchcard summary={summary} sliced={sliced} slice={slice} setSlice={setSlice} cov={cov} />
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
