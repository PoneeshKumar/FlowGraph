import { useState, useEffect } from 'react'
import Sidebar from './components/Sidebar'
import Dashboard from './components/Dashboard'
import GraphExplorer from './components/GraphExplorer'
import AlertsView from './components/AlertsView'
import TransactionsView from './components/TransactionsView'

export default function App() {
  const [view, setView] = useState('dashboard')
  const [theme, setTheme] = useState(() => localStorage.getItem('fg-theme') || 'light')

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('fg-theme', theme)
  }, [theme])

  const toggleTheme = () => setTheme(t => t === 'light' ? 'dark' : 'light')

  const views = {
    dashboard:    <Dashboard onNav={setView} />,
    graph:        <GraphExplorer />,
    alerts:       <AlertsView />,
    transactions: <TransactionsView />,
  }

  return (
    <>
      <Sidebar active={view} onNav={setView} theme={theme} onToggleTheme={toggleTheme} />
      {views[view]}
    </>
  )
}
