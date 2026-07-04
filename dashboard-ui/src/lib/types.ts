export interface BirdInfo {
  group?: string
  habitat?: string
  season?: string
  sound?: string
  note?: string
}

export interface ClipRef {
  seg: number
  s: number
  e: number
  conf: number
}

export interface Species {
  name: string
  sci: string
  slug: string
  /** realistic illustration exists (served at /sprite/<slug>.png) */
  art: boolean
  /** pixel sprite exists (served at /psprite/<slug>.png) */
  pixel: boolean
  total: number
  peak: number
  first: string
  last: string
  clip?: ClipRef
  info?: BirdInfo
}

/** [dayIndex, hour, speciesIndex, confBucket, count] */
export type CubeRow = [number, number, number, number, number]

export interface Summary {
  meta: {
    generated_at: string
    location: string
    tz: string
    min_conf: number
    conf_edges: number[]
    excluded_days: string[]
  }
  days: string[]
  sun: { rise: number; set: number }[]
  species: Species[]
  cube: CubeRow[]
}

export type Band = 'all' | 'dawn' | 'day' | 'dusk' | 'night'
export type RangeKey = '7d' | '14d' | '30d' | 'all'

export interface Slice {
  range: RangeKey
  /** single-day focus (day index) — narrows the window to one day */
  day: number | null
  band: Band
  /** exact hour focus (0–23) from rhythm/punchcard clicks */
  hour: number | null
  /** minimum confidence bucket: 0=all, 1=≥.5, 2=≥.7, 3=≥.9 */
  confMin: 0 | 1 | 2 | 3
  /** focus a single species (index into summary.species) */
  species: number | null
}

export const DEFAULT_SLICE: Slice = {
  range: '30d',
  day: null,
  band: 'all',
  hour: null,
  confMin: 0,
  species: null,
}
