import { useEffect, useState } from 'react'
import type { SureSummary } from '../lib/types'
import { fmtInt } from '../lib/format'
import { useTheme } from '../theme'
import { Sprite } from './Sprite'

const INITIAL_LIMIT = 30

export function SureCalls() {
  const { theme, toggle } = useTheme()
  const [summary, setSummary] = useState<SureSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/dash/sure', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<SureSummary>
      })
      .then(setSummary)
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return
        setError(String(reason))
      })
    return () => controller.abort()
  }, [])

  if (error) {
    return (
      <div className="wrap">
        <div className="panel sure-state">
          <p>couldn’t load sure calls ({error})</p>
          <a className="chip" href="/dash/">Back to dashboard</a>
        </div>
      </div>
    )
  }

  if (!summary) {
    return (
      <div className="wrap">
        <div className="loading lbl">finding the clearest calls…</div>
      </div>
    )
  }

  const shown = expanded ? summary.species : summary.species.slice(0, INITIAL_LIMIT)

  return (
    <div className="wrap">
      <header className="masthead">
        <div>
          <h1 className="site-title">
            {theme === 'game' ? 'BIRDEX' : 'Backyard Birds'}
          </h1>
          <div className="lbl">
            {summary.meta.location} · all time · confidence {summary.meta.min_conf.toFixed(2)}+
          </div>
          <p className="sure-intro">
            Sure calls ranks the birds heard most often when BirdNET was at least 90% confident.
          </p>
        </div>
        <div className="masthead-right">
          <button className="chip theme-toggle" onClick={toggle}>
            {theme === 'game' ? 'Clean mode' : 'Game mode'}
          </button>
          <a className="lbl" href="/dash/">
            dashboard ↗
          </a>
        </div>
      </header>

      <section aria-labelledby="sure-heading">
        <h2 className="sure-heading" id="sure-heading">
          Sure calls
          <span>{summary.species.length} species at 0.90+</span>
        </h2>

        {shown.length ? (
          <ol className="sure-list">
            {shown.map((species, index) => (
              <li className="panel sure-species" key={species.name}>
                <div className="sure-rank num">{String(index + 1).padStart(2, '0')}</div>
                <Sprite sp={species} size={88} />
                <div className="sure-details">
                  <div className="sure-name-row">
                    <div>
                      <h3>{species.name}</h3>
                      <div className="dex-entry-sci">{species.sci}</div>
                    </div>
                    <div className="sure-count num">
                      <strong>×{fmtInt(species.count)}</strong>
                      <span>sure hears</span>
                    </div>
                  </div>
                  {species.info?.sound && (
                    <p className="sure-sound">“{species.info.sound}”</p>
                  )}
                  <div className="sure-clips">
                    {species.clips.length ? (
                      species.clips.map((clip, clipIndex) => (
                        <div className="sure-clip" key={`${clip.seg}-${clip.s}-${clip.e}`}>
                          <div className="lbl">
                            Sample {clipIndex + 1} · conf {clip.conf.toFixed(2)}
                          </div>
                          <audio
                            controls
                            preload="none"
                            src={`/audio/${clip.seg}?start=${clip.s}&end=${clip.e}`}
                          />
                        </div>
                      ))
                    ) : (
                      <div className="lbl sure-no-audio">no recording kept on this machine</div>
                    )}
                  </div>
                  <div className="sure-footer">
                    <span className="lbl">peak {species.peak.toFixed(2)}</span>
                    <a className="lbl" href={`/bird/${species.slug}`}>
                      All clips ↗
                    </a>
                  </div>
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <div className="panel sure-empty">No detections have reached 0.90 confidence yet.</div>
        )}
      </section>

      {!expanded && summary.species.length > INITIAL_LIMIT && (
        <button className="chip sure-more" onClick={() => setExpanded(true)}>
          Show all {summary.species.length} species
        </button>
      )}

      <footer className="lbl sure-page-footer">
        data refreshed {summary.meta.generated_at.replace('T', ' ')}
      </footer>
    </div>
  )
}
