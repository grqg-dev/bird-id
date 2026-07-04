import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

export type Theme = 'clean' | 'game'

const ThemeContext = createContext<{ theme: Theme; toggle: () => void }>({
  theme: 'clean',
  toggle: () => {},
})

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem('dash-theme')
    return saved === 'game' ? 'game' : 'clean'
  })

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('dash-theme', theme)
  }, [theme])

  const toggle = useCallback(
    () => setTheme((t) => (t === 'clean' ? 'game' : 'clean')),
    [],
  )

  return <ThemeContext.Provider value={{ theme, toggle }}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  return useContext(ThemeContext)
}
