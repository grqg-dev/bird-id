import { useState } from 'react'
import type { Sliced } from '../lib/slice'
import type { Summary } from '../lib/types'
import { fables, pickFable } from '../lib/fable'

/**
 * One small true story from the current slice, under the header.
 * Click it to hear another. Every line is computed from real aggregates.
 */
export function FieldNote({ summary, sliced }: { summary: Summary; sliced: Sliced }) {
  const [offset, setOffset] = useState(0)
  const lines = fables(summary, sliced)
  const key = `${sliced.window.lo}-${sliced.window.hi}-${sliced.total}`
  const line = pickFable(lines, key, offset)
  return (
    <button
      className="field-note"
      title="Another note"
      onClick={() => setOffset((o) => o + 1)}
    >
      {line}
    </button>
  )
}
