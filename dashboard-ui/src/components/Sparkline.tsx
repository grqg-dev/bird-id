/** Tiny inline area sparkline for per-day series. */
export function Sparkline({
  data,
  width = 96,
  height = 22,
  fluid = false,
}: {
  data: number[]
  width?: number
  height?: number
  /** scale to the container width instead of a fixed pixel width */
  fluid?: boolean
}) {
  const max = Math.max(1, ...data)
  const n = Math.max(2, data.length)
  const pts = data.map((v, i) => {
    const x = (i / (n - 1)) * (width - 2) + 1
    const y = height - 1 - (v / max) * (height - 4)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  return (
    <svg
      width={fluid ? '100%' : width}
      height={height}
      viewBox={fluid ? `0 0 ${width} ${height}` : undefined}
      preserveAspectRatio="none"
      aria-hidden
      style={{ display: 'block' }}
    >
      <polyline
        points={pts.join(' ')}
        fill="none"
        stroke="var(--heat3)"
        strokeWidth={1.6}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}
