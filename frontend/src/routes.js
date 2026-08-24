import { useEffect, useState } from 'react'
import { Coins, Gauge, LineChart } from 'lucide-react'

/**
 * Hash routing, not path routing.
 *
 * FastAPI serves the built bundle with `StaticFiles`, so a real path like
 * `/analytics` would 404 on refresh unless the server learned to rewrite it.
 * A hash keeps every page directly linkable and reload-safe in both the Vite
 * dev server and the single-process deployment.
 */
export const NAV = [
  { path: '/', label: 'Operations', hint: 'Live control', icon: Gauge },
  { path: '/analytics', label: 'Analytics', hint: 'Charts & history', icon: LineChart },
  { path: '/pricing', label: 'Pricing', hint: 'Tariff configuration', icon: Coins },
]

const DEFAULT_ROUTE = '/'

function readRoute() {
  const raw = window.location.hash.replace(/^#/, '')
  return NAV.some((item) => item.path === raw) ? raw : DEFAULT_ROUTE
}

export function useHashRoute() {
  const [route, setRoute] = useState(readRoute)

  useEffect(() => {
    const onChange = () => setRoute(readRoute())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  // Scrolling back to the top on navigation — pages are tall enough to need it.
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [route])

  return route
}
