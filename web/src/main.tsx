import '@fontsource/dm-mono/300.css'
import '@fontsource/dm-mono/400.css'
import '@fontsource/dm-mono/500.css'
import '@fontsource/dm-serif-display/400.css'
import '@fontsource/dm-serif-display/400-italic.css'
import './styles/globals.css'
import './styles/rabbit.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { registerServiceWorker } from './lib/push'

registerServiceWorker()
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
