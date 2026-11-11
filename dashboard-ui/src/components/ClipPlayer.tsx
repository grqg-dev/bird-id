import { useState } from 'react'
import type { ClipRef } from '../lib/types'
import { keepLabel } from '../lib/keep'

const q = (c: ClipRef) => `start=${c.s}&end=${c.e}`

/**
 * Play button + on-demand spectrogram for one retained clip, with the retention
 * note. Only rendered where audio exists. Audio and the spectrogram are not
 * requested until the person asks (preload none, image mounted on click).
 */
export function ClipPlayer({
  clip,
  compact = false,
  label = true,
}: {
  clip: ClipRef
  compact?: boolean
  label?: boolean
}) {
  const [playing, setPlaying] = useState(false)
  const [spec, setSpec] = useState(false)
  return (
    <div className={`clip-player ${compact ? 'compact' : ''}`}>
      <div className="clip-controls">
        <button
          className={`chip clip-btn ${playing ? 'on' : ''}`}
          aria-pressed={playing}
          aria-label={playing ? 'Stop recording' : 'Play recording'}
          onClick={() => setPlaying(!playing)}
        >
          {playing ? '■' : '▶'}
        </button>
        <button
          className={`chip clip-btn ${spec ? 'on' : ''}`}
          aria-pressed={spec}
          aria-label="Show spectrogram"
          title="Spectrogram"
          onClick={() => setSpec(!spec)}
        >
          ≋
        </button>
        {label && <span className="lbl clip-keep">{keepLabel(clip)}</span>}
      </div>
      {playing && (
        <audio
          autoPlay
          preload="none"
          src={`/audio/${clip.seg}?${q(clip)}`}
          onEnded={() => setPlaying(false)}
          onError={() => setPlaying(false)}
        />
      )}
      {spec && (
        <img
          className="clip-spec"
          alt="Spectrogram of the recording"
          src={`/spectrogram/${clip.seg}.png?${q(clip)}`}
        />
      )}
    </div>
  )
}
