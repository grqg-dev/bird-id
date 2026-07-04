import { createContext, useCallback, useContext, useRef, useState } from 'react'
import type { ReactNode } from 'react'

interface TipState {
  x: number
  y: number
  content: ReactNode
}

interface TipApi {
  show: (e: { clientX: number; clientY: number }, content: ReactNode) => void
  hide: () => void
}

const TipContext = createContext<TipApi>({ show: () => {}, hide: () => {} })

/** One shared fixed-position tooltip for every chart on the page. */
export function TipProvider({ children }: { children: ReactNode }) {
  const [tip, setTip] = useState<TipState | null>(null)
  const raf = useRef(0)

  const show = useCallback<TipApi['show']>((e, content) => {
    cancelAnimationFrame(raf.current)
    raf.current = requestAnimationFrame(() =>
      setTip({ x: e.clientX, y: e.clientY, content }),
    )
  }, [])
  const hide = useCallback(() => {
    cancelAnimationFrame(raf.current)
    setTip(null)
  }, [])

  return (
    <TipContext.Provider value={{ show, hide }}>
      {children}
      {tip && (
        <div className="tip" style={{ left: tip.x, top: tip.y }}>
          {tip.content}
        </div>
      )}
    </TipContext.Provider>
  )
}

export function useTip(): TipApi {
  return useContext(TipContext)
}
