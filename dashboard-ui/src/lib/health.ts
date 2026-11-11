import type { Health, Summary } from './types'

/**
 * Per-hour audio coverage derived from /api/dash/health, lined up with the
 * summary's day axis. "Unknown" (null) means we have no capture record for
 * that hour — before the first capture_health row, or in the future — and is
 * never treated as deaf.
 */
export interface Coverage {
  /** seconds of audio received that hour, or null when unknown */
  recorded: (di: number, h: number) => number | null
  /** seconds the hour should have held so far (3600, or less for the current hour) */
  expected: (di: number, h: number) => number | null
  /** true when the hour captured under the low-coverage threshold */
  hourLow: (di: number, h: number) => boolean
  /** true when the whole day captured under the threshold */
  dayLow: (di: number) => boolean
  /** recorded / expected hours for a day, null when unknown */
  dayHours: (di: number) => { recorded: number; expected: number } | null
}

export function buildCoverage(summary: Summary, health: Health | null): Coverage | null {
  if (!health || !health.first_hour) return null
  const first = health.first_hour
  const now = new Date(health.now).getTime()
  const low = health.thresholds?.low_cell ?? 0.5
  const days = summary.days

  const cell = (di: number, h: number): { rec: number; exp: number } | null => {
    const day = days[di]
    if (!day) return null
    const start = `${day}T${String(h).padStart(2, '0')}:00:00`
    if (start < first) return null
    const exp = Math.min(3600, (now - new Date(start).getTime()) / 1000)
    if (exp <= 0) return null
    return { rec: health.hourly_seconds[day]?.[h] ?? 0, exp }
  }

  const dayHours = (di: number) => {
    let rec = 0
    let exp = 0
    for (let h = 0; h < 24; h++) {
      const c = cell(di, h)
      if (!c) continue
      rec += Math.min(c.rec, c.exp)
      exp += c.exp
    }
    return exp > 0 ? { recorded: rec / 3600, expected: exp / 3600 } : null
  }

  return {
    recorded: (di, h) => cell(di, h)?.rec ?? null,
    expected: (di, h) => cell(di, h)?.exp ?? null,
    hourLow: (di, h) => {
      const c = cell(di, h)
      return c !== null && c.rec / c.exp < low
    },
    dayLow: (di) => {
      const d = dayHours(di)
      return d !== null && d.recorded / d.expected < low
    },
    dayHours,
  }
}

/** '5.4 of 24 h audio' style detail for tooltips. */
export function fmtRecorded(recorded: number, expected: number): string {
  return `${recorded.toFixed(1)} of ${expected.toFixed(expected % 1 ? 1 : 0)} h of audio`
}
