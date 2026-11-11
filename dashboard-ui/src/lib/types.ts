export interface BirdInfo {
  group?: string
  habitat?: string
  season?: string
  sound?: string
  note?: string
}

/** Why retention keeps a clip and until when (until = ISO date, null = no expiry). */
export interface ClipKeep {
  reason: 'live_window' | 'species_best' | 'high_conf' | 'mid_conf' | 'low_conf' | string
  until: string | null
}

export interface ClipRef {
  seg: number
  s: number
  e: number
  conf: number
  keep?: ClipKeep
}

/** /api/dash/species/<slug> */
export interface SpeciesDetail {
  name: string
  sci: string
  slug: string
  art: boolean
  pixel: boolean
  status: YardStatus
  total: number
  peak: number
  first: string
  last: string
  days: number
  hourly: number[]
  daily: Record<string, number>
  info: BirdInfo | null
  clips: (ClipRef & { heard_at: string })[]
  page: number
  has_more: boolean
}

/** /api/recent event (slim) */
export interface RecentEvent {
  heard_at: string
  common_name: string
  slug: string
  peak_conf: number
  segment_id: number
  start_time: number
  end_time: number
  has_sprite: boolean
}

export type YardStatus = 'expected' | 'possible' | 'unlikely'

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
  /** distinct days this species was detected */
  days: number
  /**
   * When the species became a "new encounter" — its first 0.7+ detection or its
   * second separate day, whichever came first. null = not an encounter yet.
   */
  encounter: string | null
  /** yard plausibility from docs/yard_list.json */
  status: YardStatus
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
    /** species marked 'unlikely' are included (toggle on) */
    include_unlikely: boolean
    /** unlikely species with detections, whether shown or hidden */
    unlikely_total: number
    /** how many are currently hidden from this payload */
    unlikely_hidden: number
  }
  days: string[]
  sun: { rise: number; set: number }[]
  species: Species[]
  cube: CubeRow[]
}

export interface SureSpecies {
  name: string
  sci: string
  slug: string
  art: boolean
  pixel: boolean
  count: number
  peak: number
  status: YardStatus
  clips: ClipRef[]
  info?: BirdInfo
}

export interface SureSummary {
  meta: {
    generated_at: string
    location: string
    min_conf: number
    excluded_days: string[]
    include_unlikely: boolean
    unlikely_total: number
    unlikely_hidden: number
  }
  species: SureSpecies[]
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

export interface HealthWindow {
  expected_hours: number
  recorded_hours: number
  /** 0–1, null when nothing is expected yet */
  coverage: number | null
  mean_dbfs: number | null
  median_dbfs?: number | null
}

/** /api/dash/health — capture coverage from the monitor's capture_health table. */
export interface Health {
  now: string
  first_hour: string | null
  newest_segment_at: string | null
  newest_segment_age_minutes: number | null
  last_24h: HealthWindow | null
  last_30d: HealthWindow | null
  /** 'YYYY-MM-DD' → 24 values: seconds of audio actually received that hour */
  hourly_seconds: Record<string, number[]>
  /** plain-text banner lines (empty when capture looks healthy) */
  alerts: string[]
  thresholds: { low_cell: number }
}

/** Day (YYYY-MM-DD) a species became a new encounter, or null if it has not yet. */
export function encounterDay(sp: Species): string | null {
  return sp.encounter ? sp.encounter.slice(0, 10) : null
}
