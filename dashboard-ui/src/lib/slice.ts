import type { Band, CubeRow, Slice, Summary } from './types'

/**
 * Classify an hour of a given day into a solar band using that day's
 * sunrise/sunset. Dawn = the chorus window around first light.
 */
export function bandOf(summary: Summary, di: number, hour: number): Band {
  const sun = summary.sun[di] ?? { rise: 6, set: 19.5 }
  const h = hour + 0.5
  if (h >= sun.rise - 1 && h < sun.rise + 2) return 'dawn'
  if (h >= sun.set - 1 && h < sun.set + 1) return 'dusk'
  if (h >= sun.rise + 2 && h < sun.set - 1) return 'day'
  return 'night'
}

export interface Windowed {
  lo: number
  hi: number
  priorLo: number
  priorHi: number
}

export function windowOf(summary: Summary, slice: Slice): Windowed {
  const last = summary.days.length - 1
  if (slice.day !== null) {
    const d = Math.max(0, Math.min(last, slice.day))
    return { lo: d, hi: d, priorLo: d - 1, priorHi: d - 1 }
  }
  if (slice.range === 'all') return { lo: 0, hi: last, priorLo: 0, priorHi: -1 }
  const n = { '7d': 7, '14d': 14, '30d': 30 }[slice.range]
  const lo = Math.max(0, last - n + 1)
  const len = last - lo + 1
  return { lo, hi: last, priorLo: Math.max(0, lo - len), priorHi: lo - 1 }
}

export interface Sliced {
  window: Windowed
  /** everything below respects all active filters unless noted */
  total: number
  speciesCount: number
  perSpecies: number[]
  perSpeciesPrior: number[]
  perSpeciesDays: number[]
  perSpeciesNight: number[]
  /** all days, for calendar navigation (ignores the day-focus filter) */
  perDayNav: number[]
  /** all 24 hours within the day window (ignores band/hour focus) */
  perHourNav: number[]
  /** per-species per-day counts within window, for sparklines (full-length day arrays) */
  speciesDaily: () => Map<number, number[]>
  /** day × hour matrix over the whole recorded span (conf+species filtered) */
  punchcard: number[][]
  busiestDay: { di: number; count: number } | null
  busiestHour: { hour: number; count: number } | null
  /** species indexes first heard inside the window (uses absolute first-heard) */
  discoveredInWindow: number[]
}

/** Single pass over the cube, accumulating every widget's aggregate. */
export function computeSlice(summary: Summary, slice: Slice): Sliced {
  const nDays = summary.days.length
  const nSpecies = summary.species.length
  const win = windowOf(summary, slice)

  const perSpecies = new Array<number>(nSpecies).fill(0)
  const perSpeciesPrior = new Array<number>(nSpecies).fill(0)
  const perSpeciesNight = new Array<number>(nSpecies).fill(0)
  const perDayNav = new Array<number>(nDays).fill(0)
  const perHourNav = new Array<number>(24).fill(0)
  const punchcard: number[][] = Array.from({ length: nDays }, () =>
    new Array<number>(24).fill(0),
  )
  const daysSeen: Set<number>[] = Array.from({ length: nSpecies }, () => new Set())
  const perDayHourSpecies = new Map<number, number[]>()

  let total = 0
  const hourOk = (di: number, h: number) => {
    if (slice.hour !== null && h !== slice.hour) return false
    if (slice.band !== 'all' && bandOf(summary, di, h) !== slice.band) return false
    return true
  }

  for (const [di, h, si, cb, n] of summary.cube as CubeRow[]) {
    if (cb < slice.confMin) continue
    const speciesOk = slice.species === null || si === slice.species
    const inWin = di >= win.lo && di <= win.hi
    const inPrior = di >= win.priorLo && di <= win.priorHi
    const hOk = hourOk(di, h)

    // navigation surfaces keep their own axis unfiltered
    if (speciesOk && hOk) perDayNav[di] += n
    if (speciesOk && inWin) perHourNav[h] += n
    if (speciesOk) punchcard[di][h] += n

    if (!hOk) continue
    if (inPrior) perSpeciesPrior[si] += n
    if (!inWin) continue

    perSpecies[si] += n
    daysSeen[si].add(di)
    if (bandOf(summary, di, h) === 'night') perSpeciesNight[si] += n
    if (speciesOk) {
      total += n
      let row = perDayHourSpecies.get(si)
      if (!row) perDayHourSpecies.set(si, (row = new Array(nDays).fill(0)))
      row[di] += n
    }
  }

  let speciesCount = 0
  for (const c of perSpecies) if (c > 0) speciesCount++

  let busiestDay: Sliced['busiestDay'] = null
  for (let di = win.lo; di <= win.hi; di++) {
    let dayTotal = 0
    for (let h = 0; h < 24; h++) {
      if (!hourOk(di, h)) continue
      if (slice.species !== null) {
        dayTotal = perDayHourSpecies.get(slice.species)?.[di] ?? 0
        break
      }
      dayTotal += punchcard[di][h]
    }
    if (!busiestDay || dayTotal > busiestDay.count) busiestDay = { di, count: dayTotal }
  }
  if (busiestDay && busiestDay.count === 0) busiestDay = null

  let busiestHour: Sliced['busiestHour'] = null
  perHourNav.forEach((c, hour) => {
    if (!busiestHour || c > busiestHour.count) busiestHour = { hour, count: c }
  })
  if (busiestHour && (busiestHour as { count: number }).count === 0) busiestHour = null

  const discoveredInWindow: number[] = []
  for (let si = 0; si < nSpecies; si++) {
    const firstDay = summary.species[si].first.slice(0, 10)
    const fi = summary.days.indexOf(firstDay)
    if (fi >= win.lo && fi <= win.hi && perSpecies[si] > 0) discoveredInWindow.push(si)
  }

  const perSpeciesDays = daysSeen.map((s) => s.size)

  return {
    window: win,
    total,
    speciesCount,
    perSpecies,
    perSpeciesPrior,
    perSpeciesDays,
    perSpeciesNight,
    perDayNav,
    perHourNav,
    speciesDaily: () => perDayHourSpecies,
    punchcard,
    busiestDay,
    busiestHour,
    discoveredInWindow,
  }
}

/** Ranked species indexes (by windowed count, desc), only those present. */
export function ranked(sliced: Sliced): number[] {
  return sliced.perSpecies
    .map((count, si) => ({ si, count }))
    .filter((r) => r.count > 0)
    .sort((a, b) => b.count - a.count || a.si - b.si)
    .map((r) => r.si)
}
