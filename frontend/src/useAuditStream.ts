import { useEffect, useRef, useState } from 'react'

import { API_BASE } from './api'
import type { AuditEntry, FeedEntry } from './types'

export type StreamStatus = 'connecting' | 'live' | 'offline'

/**
 * The decision feed, streamed.
 *
 * `EventSource` rather than polling because CLAUDE.md is specific that the
 * dashboard must stream: a row flipping to FAIL while you are still saying the
 * sentence is the moment the demo turns on, and a refresh button is not that.
 *
 * **No `?since=` here, deliberately.** The URL is fixed for the connection's
 * lifetime and the API lets `since` win over `Last-Event-ID`, so an automatic
 * reconnect would replay from the same point forever. Omitting it lets the
 * browser resume properly on its own; the cursor de-duplication below then
 * makes a replay harmless whichever way it happens.
 */
export function useAuditStream() {
  const [entries, setEntries] = useState<FeedEntry[]>([])
  const [status, setStatus] = useState<StreamStatus>('connecting')
  const seen = useRef<Set<number>>(new Set())

  useEffect(() => {
    const source = new EventSource(`${API_BASE}/audit/stream`)

    source.onopen = () => setStatus('live')

    // Every frame names its event type, so there is no default `message` event
    // to listen for -- these have to be bound by name.
    const onFrame = (event: MessageEvent) => {
      const cursor = Number(event.lastEventId)
      if (!Number.isFinite(cursor) || seen.current.has(cursor)) return
      seen.current.add(cursor)
      try {
        const entry = JSON.parse(event.data) as AuditEntry
        setEntries((current) => [...current, { cursor, entry }])
      } catch {
        // A frame we cannot parse is a frame we cannot render. Dropping it
        // beats tearing down a live feed mid-demo over one bad row.
      }
    }

    const types = [
      'MANDATE_MINTED',
      'MANDATE_BOUND',
      'MANDATE_REVOKED',
      'MANDATE_CONSUMED',
      'KILL_SWITCH_SET',
      'CANDIDATE_SELECTED',
      'EVALUATION_COMPLETE',
      'VERDICT_ISSUED',
      'SETTLEMENT_SETTLED',
      'SETTLEMENT_REFUSED',
      'SETTLEMENT_FAILED',
    ]
    types.forEach((type) => source.addEventListener(type, onFrame))

    // EventSource reconnects on its own, so this is a status change and not a
    // failure -- reporting it as one would have the UI cry wolf on every blip.
    source.onerror = () => setStatus(source.readyState === 2 ? 'offline' : 'connecting')

    return () => source.close()
  }, [])

  return { entries, status }
}
