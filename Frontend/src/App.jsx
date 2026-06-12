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
      {/* Ambient light field behind the glass */}
      <div className="aurora" aria-hidden="true">
        <div className="aurora-orb aurora-orb-1" />
        <div className="aurora-orb aurora-orb-2" />
        <div className="aurora-orb aurora-orb-3" />
      </div>

      <Sidebar
        active={view}
        onNav={setView}
        theme={theme}
        onToggleTheme={() => setTheme(t => (t === 'light' ? 'dark' : 'light'))}
      />

      <main className="relative z-[1] flex-1 min-w-0 h-screen overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.div
            key={view}
            className="h-full"
            initial={{ opacity: 0, y: 14, filter: 'blur(6px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, y: -10, filter: 'blur(6px)' }}
            transition={{ duration: 0.32, ease: [0.32, 0.72, 0, 1] }}
          >
            <ActiveView onNav={setView} />
          </motion.div>
        </AnimatePresence>
      </main>
    </>
  )
}
