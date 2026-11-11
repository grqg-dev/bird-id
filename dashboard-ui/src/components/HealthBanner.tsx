import type { Health } from '../lib/types'

/**
 * Plain-text warning when the mic isn't delivering: low 24 h coverage, a stale
 * newest segment, or a level drop. The server decides (see build_dash_health);
 * this just shows its lines.
 */
export function HealthBanner({ health }: { health: Health | null }) {
  if (!health || health.alerts.length === 0) return null
  return (
    <div className="health-banner" role="alert">
      <strong className="health-banner-title">Capture problem</strong>
      <ul>
        {health.alerts.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
      <span className="lbl">
        Counts below may undercount real activity. Hatched days and hours had under half their
        audio.
      </span>
    </div>
  )
}
