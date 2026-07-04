import type { Sliced } from './slice'
import type { Summary } from './types'
import { ranked } from './slice'
import { fmtClock, fmtDay, fmtHour, fmtInt } from './format'

/**
 * The fable line: one small true story from the current slice of data.
 * Every candidate is derived from real aggregates; nothing is invented.
 * Deterministic per slice (so it doesn't flicker), cyclable by the reader.
 */
export function fables(summary: Summary, sliced: Sliced): string[] {
  const out: string[] = []
  const order = ranked(sliced)

  // rarest visitor in the window
  const rare = [...order].reverse().find((si) => sliced.perSpeciesDays[si] === 1)
  if (rare !== undefined) {
    const sp = summary.species[rare]
    const count = sliced.perSpecies[rare]
    out.push(
      count === 1
        ? `The ${sp.name.toLowerCase()} spoke exactly once, and was not heard again.`
        : `The ${sp.name.toLowerCase()} came for a single day, called ${fmtInt(count)} times, and moved on.`,
    )
  }

  // the constant companion
  const winLen = sliced.window.hi - sliced.window.lo + 1
  const faithful = order.find((si) => sliced.perSpeciesDays[si] === winLen)
  if (faithful !== undefined && winLen > 2) {
    const sp = summary.species[faithful]
    out.push(
      `The ${sp.name.toLowerCase()} has not missed a day: all ${winLen} of them, ${fmtInt(sliced.perSpecies[faithful])} calls.`,
    )
  }

  // night voices
  const nightOwl = order.find(
    (si) =>
      sliced.perSpeciesNight[si] > 10 &&
      sliced.perSpeciesNight[si] > sliced.perSpecies[si] * 0.5,
  )
  if (nightOwl !== undefined) {
    const sp = summary.species[nightOwl]
    out.push(
      `While the yard slept, the ${sp.name.toLowerCase()} kept talking: ${fmtInt(sliced.perSpeciesNight[nightOwl])} calls in the dark.`,
    )
  }

  // the dawn chorus
  if (sliced.busiestHour) {
    const sun = summary.sun[sliced.window.hi]
    const nearDawn = Math.abs(sliced.busiestHour.hour + 0.5 - sun.rise) <= 1.5
    out.push(
      nearDawn
        ? `The day's loudest hour is ${fmtHour(sliced.busiestHour.hour)}, right as the sun clears the ridge at ${fmtClock(sun.rise)}.`
        : `The loudest hour was ${fmtHour(sliced.busiestHour.hour)}, with ${fmtInt(sliced.busiestHour.count)} calls.`,
    )
  }

  // newest arrival
  const newest = sliced.discoveredInWindow
    .slice()
    .sort((a, b) => summary.species[a].first.localeCompare(summary.species[b].first))
    .pop()
  if (newest !== undefined && sliced.window.hi > sliced.window.lo) {
    const sp = summary.species[newest]
    out.push(
      `Newest voice in the yard: a ${sp.name.toLowerCase()}, first heard ${fmtDay(sp.first.slice(0, 10))}.`,
    )
  }

  // sheer volume
  if (sliced.busiestDay && order.length > 0) {
    const top = summary.species[order[0]]
    out.push(
      `${fmtInt(sliced.total)} calls from ${sliced.speciesCount} species; the ${top.name.toLowerCase()} did most of the talking.`,
    )
  }

  if (out.length === 0) out.push('A quiet stretch. The microphone is listening.')
  return out
}

/** Stable pick for a given slice; `offset` lets the reader page through. */
export function pickFable(list: string[], key: string, offset: number): string {
  let h = 0
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0
  const idx = (((h + offset) % list.length) + list.length) % list.length
  return list[idx]
}
