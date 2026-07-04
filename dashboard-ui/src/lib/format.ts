export function fmtInt(n: number): string {
  return n.toLocaleString('en-US')
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** '2026-06-19' → 'Jun 19' */
export function fmtDay(iso: string): string {
  const m = Number(iso.slice(5, 7))
  const d = Number(iso.slice(8, 10))
  return `${MONTHS[m - 1]} ${d}`
}

/** 5 → '5am', 13 → '1pm', 0 → '12am' */
export function fmtHour(h: number): string {
  const hh = ((h % 24) + 24) % 24
  if (hh === 0) return '12am'
  if (hh === 12) return '12pm'
  return hh < 12 ? `${hh}am` : `${hh - 12}pm`
}

/** decimal hours → '5:47am' */
export function fmtClock(dec: number): string {
  const h = Math.floor(dec)
  const m = Math.round((dec - h) * 60)
  const hh = ((h % 24) + 24) % 24
  const base = hh % 12 === 0 ? 12 : hh % 12
  return `${base}:${String(m).padStart(2, '0')}${hh < 12 ? 'am' : 'pm'}`
}

/** ISO timestamp → 'Jun 19 · 5:47am' */
export function fmtWhen(iso: string): string {
  const h = Number(iso.slice(11, 13))
  const m = iso.slice(14, 16)
  const base = h % 12 === 0 ? 12 : h % 12
  return `${fmtDay(iso.slice(0, 10))} · ${base}:${m}${h < 12 ? 'am' : 'pm'}`
}
