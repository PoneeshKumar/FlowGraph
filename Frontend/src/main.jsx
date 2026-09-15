import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { DataSourceProvider } from './services/DataSourceProvider'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <DataSourceProvider>
      <App />
    </DataSourceProvider>
  </StrictMode>,
)
