import { useEffect, useRef, useState } from 'react'
import type { PriceUpdate } from './types'

export type PriceMap = Record<string, PriceUpdate>

/**
 * Live price feed: connects to the backend WebSocket, receives an initial
 * snapshot and then ~1/second batched updates. Reconnects automatically.
 */
export function usePrices(): { prices: PriceMap; connected: boolean } {
  const [prices, setPrices] = useState<PriceMap>({})
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let closed = false
    let retry: ReturnType<typeof setTimeout>

    function connect() {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${window.location.host}/ws/prices`)
      wsRef.current = ws

      ws.onopen = () => setConnected(true)
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data) as { type: string; data: PriceUpdate[] }
        setPrices((prev) => {
          const next = msg.type === 'snapshot' ? {} : { ...prev }
          for (const entry of msg.data) next[entry.market] = entry
          return next
        })
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) retry = setTimeout(connect, 3000)
      }
    }

    connect()
    return () => {
      closed = true
      clearTimeout(retry)
      wsRef.current?.close()
    }
  }, [])

  return { prices, connected }
}
