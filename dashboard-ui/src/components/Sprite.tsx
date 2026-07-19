import { useState } from 'react'
import type { Species } from '../lib/types'
import { useTheme } from '../theme'

type SpriteSpecies = Pick<Species, 'name' | 'slug' | 'art' | 'pixel'>

function initials(name: string): string {
  return name
    .split(/[\s-]+/)
    .filter(Boolean)
    .slice(0, 3)
    .map((w) => w[0].toUpperCase())
    .join('')
}

/**
 * Species artwork: pixel sprite in game mode, illustration in clean mode,
 * with a lettered tile fallback when no art exists for the skin.
 */
export function Sprite({ sp, size = 64 }: { sp: SpriteSpecies; size?: number }) {
  const { theme } = useTheme()
  const [broken, setBroken] = useState(false)
  const pixelFirst = theme === 'game'
  const src = pixelFirst
    ? sp.pixel
      ? `/psprite/${sp.slug}.png`
      : sp.art
        ? `/sprite/${sp.slug}.png`
        : null
    : sp.art
      ? `/sprite/${sp.slug}.png`
      : sp.pixel
        ? `/psprite/${sp.slug}.png`
        : null

  if (!src || broken) {
    return (
      <div
        aria-hidden
        style={{
          width: size,
          height: size,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--well)',
          border: '1px solid var(--line)',
          borderRadius: 'calc(var(--radius) - 4px)',
          color: 'var(--faint)',
          fontFamily: 'var(--font-label)',
          fontSize: Math.max(9, size / 5),
          letterSpacing: 1,
        }}
      >
        {initials(sp.name)}
      </div>
    )
  }
  return (
    <img
      className="sprite-img"
      src={src}
      alt={sp.name}
      width={size}
      height={size}
      loading="lazy"
      style={{ objectFit: 'contain', display: 'block' }}
      onError={() => setBroken(true)}
    />
  )
}
