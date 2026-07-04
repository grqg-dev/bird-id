/** Tiny inline area sparkline for per-day series. */
export function Sparkline({
  data,
  width = 96,
  height = 22,
}: {
  data: number[]
  width?: number
  height?: number
}) {
  const max = Math.max(1, ...data)
  const n = Math.max(2, data.length)
  const pts = data.map((v, i) => {
    const x = (i / (n - 1)) * (width - 2) + 1
    const y = height - 1 - (v / max) * (height - 4)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  return (
    <svg width={width} height={height} aria-hidden style={{ display: 'block' }}>
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
