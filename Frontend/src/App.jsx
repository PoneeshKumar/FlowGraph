import { useState } from 'react'
import Sidebar from './components/Sidebar'
import Dashboard from './components/Dashboard'
import GraphExplorer from './components/GraphExplorer'
import AlertsView from './components/AlertsView'
import TransactionsView from './components/TransactionsView'

export default function App() {
  const [view, setView] = useState('dashboard')

  const views = {
    dashboard:    <Dashboard onNav={setView} />,
    graph:        <GraphExplorer />,
    alerts:       <AlertsView />,
    transactions: <TransactionsView />,
  }

  return (
    <>
      <Sidebar active={view} onNav={setView} />
      {views[view]}
    </>
  )
}
