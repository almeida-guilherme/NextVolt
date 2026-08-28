import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, telemetrySocketUrls } from '../api'

const MAX_POINTS = 300 // 5 minutes at the 1 Hz control-loop rate
const RECONNECT_STEPS = [500, 1000, 2000, 4000, 8000]
const POLL_INTERVAL_MS = 2000 // REST cadence while the socket is down
const FAILOVER_DELAY_MS = 300 // pause before trying the next candidate URL
// The control loop broadcasts at 1 Hz, so this many missed ticks means the
// socket is open but not carrying the feed — a host that accepts the upgrade
// without proxying it, or a connection that died without a FIN.
const STALE_TIMEOUT_MS = 8000

/** Reshape one broadcast snapshot into a single chart row. */
function toChartRow(snapshot) {
  const row = {
    t: Date.parse(snapshot.server_time) || Date.now(),
    pv: (snapshot.site?.pv_power_w || 0) / 1000,
    limit: (snapshot.site?.power_limit_w || 0) / 1000,
    total: (snapshot.site?.evse_power_w || 0) / 1000,
    cost: snapshot.site?.session_cost || 0,
  }
  for (const connector of snapshot.connectors || []) {
    row[`c${connector.connector_id}`] = connector.power_kw || 0
  }
  return row
}

/**
 * Live station feed.
 *
 * Owns the WebSocket lifecycle (with backoff reconnect), the latest snapshot
 * and the rolling chart buffer. Also seeds that buffer from the persisted
 * telemetry samples so a page reload does not start from an empty chart.
 *
 * `/api/state` returns the exact same payload the socket pushes, so whenever
 * the socket is not open we poll it instead. That keeps the dashboard live on
 * hosts that refuse the WebSocket upgrade, and `refresh()` lets a command
 * handler pull the new state immediately instead of waiting for a tick.
 */
export function useStationSocket() {
  const [status, setStatus] = useState('connecting')
  const [snapshot, setSnapshot] = useState(null)
  const [series, setSeries] = useState([])
  const [lastMessageAt, setLastMessageAt] = useState(null)
  // Flips once, on the first snapshot from any source. A plain `snapshot`
  // check cannot drive the poll effect: it changes every tick, which would
  // retrigger the effect and fire a fresh poll on each one.
  const [hasData, setHasData] = useState(false)

  const socketRef = useRef(null)
  const timerRef = useRef(null)
  const attemptRef = useRef(0)
  const candidateRef = useRef(0)
  const closedByUs = useRef(false)

  /** Adopt one snapshot, wherever it came from (socket push or REST poll). */
  const applySnapshot = useCallback((message) => {
    if (!message || message.type !== 'state') return
    setSnapshot(message)
    setHasData(true) // no-op re-render once already true
    setLastMessageAt(Date.now())
    setSeries((current) => {
      const next = [...current, toChartRow(message)]
      return next.length > MAX_POINTS ? next.slice(next.length - MAX_POINTS) : next
    })
  }, [])

  /** Pull the current state on demand — used right after a command lands. */
  const refresh = useCallback(async () => {
    try {
      applySnapshot(await api.state())
    } catch {
      /* the socket or the next poll will catch up */
    }
  }, [applySnapshot])

  // --- seed the chart from history (once) --------------------------------
  useEffect(() => {
    let cancelled = false
    api
      .telemetryHistory(10)
      .then(({ items }) => {
        if (cancelled || !items?.length) return
        const buckets = new Map()
        for (const item of items) {
          const key = item.at.slice(0, 19)
          const bucket = buckets.get(key) || { t: Date.parse(item.at), pv: 0, cost: 0, total: 0 }
          bucket[`c${item.connector_id}`] = item.power_kw
          bucket.pv = Math.max(bucket.pv, item.pv_power_w / 1000)
          bucket.cost += item.running_cost
          bucket.total += item.power_kw
          buckets.set(key, bucket)
        }
        const seeded = [...buckets.values()].sort((a, b) => a.t - b.t).slice(-MAX_POINTS)
        setSeries((current) => (current.length ? current : seeded))
      })
      .catch(() => {
        /* history is a nicety — an empty chart fills itself within a second */
      })
    return () => {
      cancelled = true
    }
  }, [])

  // --- socket lifecycle --------------------------------------------------
  const connect = useCallback(() => {
    if (socketRef.current) return
    setStatus((current) => (current === 'open' ? current : 'connecting'))

    const urls = telemetrySocketUrls()
    // Opening is not enough: this URL only counts as working once it has
    // actually delivered a snapshot.
    let carriesFeed = false
    let staleTimer = null

    let socket
    try {
      socket = new WebSocket(urls[candidateRef.current % urls.length])
    } catch {
      scheduleReconnect()
      return
    }
    socketRef.current = socket

    const armStaleTimer = () => {
      if (staleTimer) clearTimeout(staleTimer)
      staleTimer = setTimeout(() => socket.close(), STALE_TIMEOUT_MS)
    }

    socket.onopen = () => {
      attemptRef.current = 0
      setStatus('open')
      armStaleTimer()
    }

    socket.onmessage = (event) => {
      let message
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      if (message?.type === 'state') {
        carriesFeed = true
        armStaleTimer()
      }
      applySnapshot(message)
    }

    socket.onerror = () => setStatus((current) => (current === 'open' ? 'open' : 'error'))

    socket.onclose = () => {
      if (staleTimer) clearTimeout(staleTimer)
      socketRef.current = null
      if (closedByUs.current) return
      setStatus('closed')
      // This URL never carried a snapshot — it is not the feed (a static host
      // answering the upgrade with index.html, say). Try the next candidate.
      scheduleReconnect(!carriesFeed && urls.length > 1)
    }

    function scheduleReconnect(tryNextCandidate = false) {
      if (closedByUs.current || timerRef.current) return
      let delay
      if (tryNextCandidate && candidateRef.current + 1 < urls.length) {
        // Fast failover, but only on the first pass through the list: once
        // every candidate has been tried the normal backoff must take over so
        // an unreachable backend is not hammered every 300 ms.
        candidateRef.current += 1
        delay = FAILOVER_DELAY_MS
      } else {
        if (tryNextCandidate) candidateRef.current += 1
        delay = RECONNECT_STEPS[Math.min(attemptRef.current, RECONNECT_STEPS.length - 1)]
        attemptRef.current += 1
      }
      timerRef.current = setTimeout(() => {
        timerRef.current = null
        connect()
      }, delay)
    }
  }, [applySnapshot])

  useEffect(() => {
    closedByUs.current = false
    connect()
    return () => {
      closedByUs.current = true
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = null
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [connect])

  // --- REST fallback -----------------------------------------------------
  // Runs on a cold start (first paint before the socket opens) and for as long
  // as the socket stays down. Without it a blocked upgrade freezes the whole
  // dashboard: commands reach the API but their effect is never rendered.
  useEffect(() => {
    // `hasData` keeps polling through a socket that opened but has not
    // delivered anything yet, so the first paint never waits on the watchdog.
    if (status === 'open' && hasData) return undefined
    let cancelled = false
    const poll = () => {
      api
        .state()
        .then((state) => {
          if (!cancelled) applySnapshot(state)
        })
        .catch(() => {})
    }
    poll()
    const id = setInterval(poll, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [status, hasData, applySnapshot])

  const connected = status === 'open'
  const stationOnline = Boolean(snapshot?.station?.online)

  return useMemo(
    () => ({ status, connected, stationOnline, snapshot, series, lastMessageAt, refresh }),
    [status, connected, stationOnline, snapshot, series, lastMessageAt, refresh],
  )
}
