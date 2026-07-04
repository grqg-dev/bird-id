import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './theme.css'
import './app.css'
import { ThemeProvider } from './theme'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </StrictMode>,
)
