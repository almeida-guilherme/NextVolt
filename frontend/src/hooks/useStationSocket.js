import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, telemetrySocketUrl } from '../api'

const MAX_POINTS = 300 // 5 minutes at the 1 Hz control-loop rate
const RECONNECT_STEPS = [500, 1000, 2000, 4000, 8000]

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
 */
export function useStationSocket() {
  const [status, setStatus] = useState('connecting')
  const [snapshot, setSnapshot] = useState(null)
  const [series, setSeries] = useState([])
  const [lastMessageAt, setLastMessageAt] = useState(null)

  const socketRef = useRef(null)
  const timerRef = useRef(null)
  const attemptRef = useRef(0)
  const closedByUs = useRef(false)

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

    let socket
    try {
      socket = new WebSocket(telemetrySocketUrl())
    } catch {
      scheduleReconnect()
      return
    }
    socketRef.current = socket

    socket.onopen = () => {
      attemptRef.current = 0
      setStatus('open')
    }

    socket.onmessage = (event) => {
      let message
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      if (message.type !== 'state') return
      setSnapshot(message)
      setLastMessageAt(Date.now())
      setSeries((current) => {
        const next = [...current, toChartRow(message)]
        return next.length > MAX_POINTS ? next.slice(next.length - MAX_POINTS) : next
      })
    }

    socket.onerror = () => setStatus((current) => (current === 'open' ? 'open' : 'error'))

    socket.onclose = () => {
      socketRef.current = null
      if (closedByUs.current) return
      setStatus('closed')
      scheduleReconnect()
    }

    function scheduleReconnect() {
      if (closedByUs.current || timerRef.current) return
      const delay = RECONNECT_STEPS[Math.min(attemptRef.current, RECONNECT_STEPS.length - 1)]
      attemptRef.current += 1
      timerRef.current = setTimeout(() => {
        timerRef.current = null
        connect()
      }, delay)
    }
  }, [])

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

  // Cold-start fallback: if the socket cannot be established, still show data.
  useEffect(() => {
    if (snapshot || status === 'open') return
    const id = setTimeout(() => {
      api.state().then(setSnapshot).catch(() => {})
    }, 1200)
    return () => clearTimeout(id)
  }, [snapshot, status])

  const connected = status === 'open'
  const stationOnline = Boolean(snapshot?.station?.online)

  return useMemo(
    () => ({ status, connected, stationOnline, snapshot, series, lastMessageAt }),
    [status, connected, stationOnline, snapshot, series, lastMessageAt],
  )
}
