import type { ClipRef } from './types'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** '2027-11-04' → 'Nov 2027' */
export function fmtMonthYear(iso: string): string {
  return `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`
}

/** Plain-language retention note, e.g. '0.94 · kept until Nov 2027' or 'best of species · kept'. */
export function keepLabel(clip: Pick<ClipRef, 'conf' | 'keep'>): string {
  const conf = clip.conf.toFixed(2)
  const keep = clip.keep
  if (!keep) return conf
  if (keep.reason === 'species_best') return `${conf} · best of species · kept`
  if (keep.reason === 'live_window') return `${conf} · recent · kept at least 24 h`
  return keep.until ? `${conf} · kept until ${fmtMonthYear(keep.until)}` : `${conf} · kept`
}
