import { useState } from 'react'
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

  const ActiveView = VIEWS[view]

  return (
    <div className="relative flex h-screen min-w-0 flex-col">
      <div className="backdrop" aria-hidden="true" />

      <Sidebar active={view} onNav={setView} />

      <main className="relative z-[1] min-h-0 flex-1 overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.div
            key={view}
            className="h-full"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
          >
            <ActiveView onNav={setView} />
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  )
}
