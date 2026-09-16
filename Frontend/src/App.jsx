import { lazy, Suspense } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import Sidebar from './components/Sidebar'
import Dashboard from './components/Dashboard'
import AlertsView from './components/AlertsView'
import TransactionsView from './components/TransactionsView'
import { useRoute } from './router'
import { useDataSource } from './services/DataSourceProvider'
import { useAsync } from './hooks/useAsync'
import { Skeleton } from './components/ui'

// Cytoscape is ~1 MB minified — load the graph views only when opened.
const GraphExplorer = lazy(() => import('./components/GraphExplorer'))
const PipelineView = lazy(() => import('./components/PipelineView'))
const UploadView = lazy(() => import('./components/UploadView'))

const VIEWS = {
  overview:     Dashboard,
  graph:        GraphExplorer,
  alerts:       AlertsView,
  transactions: TransactionsView,
  pipeline:     PipelineView,
  upload:       UploadView,
}

export default function App() {
  const { view, params, navigate } = useRoute()
  const { status, source, banner } = useDataSource()
  const overview = useAsync(() => (status === 'ready' ? source.stats.overview() : Promise.resolve(null)), [status])
  const ActiveView = VIEWS[view]

  return (
    <div className="relative flex h-screen min-w-0 flex-col">
      <div className="backdrop" aria-hidden="true" />
      <Sidebar active={view} onNav={navigate} alertCount={overview.data?.open_flags?.total} />
      {banner && (
        <div className="relative z-10 mx-8 mb-2 rounded-md border border-line-2 bg-hover px-3 py-1.5 text-[12px] text-ink-2">{banner}</div>
      )}
      <main className="relative z-[1] min-h-0 flex-1 overflow-hidden">
        {status !== 'ready' ? (
          <div className="px-8 pt-4"><Skeleton className="h-6 w-48" /></div>
        ) : (
          <AnimatePresence mode="wait">
            <motion.div
              key={view}
              className="h-full"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
            >
              <Suspense fallback={<div className="px-8 pt-4"><Skeleton className="h-6 w-48" /></div>}>
                <ActiveView onNav={navigate} params={params} />
              </Suspense>
            </motion.div>
          </AnimatePresence>
        )}
      </main>
    </div>
  )
}
