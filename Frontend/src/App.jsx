import { useState, useEffect } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import Sidebar from './components/Sidebar'
import Dashboard from './components/Dashboard'
import GraphExplorer from './components/GraphExplorer'
import AlertsView from './components/AlertsView'
import TransactionsView from './components/TransactionsView'

const VIEWS = {
  dashboard:    Dashboard,
  graph:        GraphExplorer,
  alerts:       AlertsView,
  transactions: TransactionsView,
}

export default function App() {
  const [view, setView] = useState('dashboard')
  const [theme, setTheme] = useState(() => localStorage.getItem('fg-theme') || 'dark')

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('fg-theme', theme)
  }, [theme])

  const ActiveView = VIEWS[view]

  return (
    <>
      <div className="backdrop" aria-hidden="true" />

      <Sidebar
        active={view}
        onNav={setView}
        theme={theme}
        onToggleTheme={() => setTheme(t => (t === 'light' ? 'dark' : 'light'))}
      />

      <main className="relative z-[1] h-screen min-w-0 flex-1 overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.div
            key={view}
            className="h-full"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.26, ease: [0.32, 0.72, 0, 1] }}
          >
            <ActiveView onNav={setView} />
          </motion.div>
        </AnimatePresence>
      </main>
    </>
  )
}
