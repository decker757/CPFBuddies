import { useMemo, useState } from 'react'

import { ApiError, approveReview, killReview, startPurchase } from './api'
import { FlowTimeline } from './components/FlowTimeline'
import { IntentBar } from './components/IntentBar'
import { OutcomeCard } from './components/OutcomeCard'
import { ReviewModal } from './components/ReviewModal'
import { buildSteps } from './steps'
import { useAuditStream } from './useAuditStream'
import type { PurchaseResponse, ReviewHold } from './types'

/**
 * The demo principal and agent.
 *
 * Hard-coded because there is no auth: CLAUDE.md's frontend section says so
 * explicitly, and inventing a login would add a surface with nothing behind it.
 * The agent id must be one the Agent Registry knows -- `backend/app/rail.py`
 * seeds this one.
 *
 * **The default principal is a placeholder and cannot settle.** `spend()` pulls
 * XSGD from the principal's own wallet, so against a real chain this must be an
 * address that actually holds the tokens and has approved the MandateRegistry.
 * Set `VITE_PRINCIPAL` to the buyer wallet before demoing on mainnet, or the
 * charge reaches the contract and reverts for want of a balance.
 */
const PRINCIPAL =
  (import.meta.env.VITE_PRINCIPAL as string | undefined) ??
  '0xabababababababababababababababababababab'
const AGENT_ID = 'browser-agent-1'
const ACTOR = 'demo-operator'
const TTL_SECONDS = 600

export default function App() {
  const { entries, status } = useAuditStream()

  // Where this run starts in the feed. The stream carries every purchase ever
  // made in this process, so a run is "everything after the moment I asked".
  const [runFrom, setRunFrom] = useState<number | null>(null)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<PurchaseResponse | null>(null)
  const [hold, setHold] = useState<ReviewHold | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const runFeed = useMemo(
    () => (runFrom === null ? [] : entries.slice(runFrom)),
    [entries, runFrom],
  )
  const steps = useMemo(
    () =>
      buildSteps(runFeed, {
        running,
        queued: Boolean(result?.queued_message_id),
      }),
    [runFeed, running, result],
  )

  const settlementEntry =
    runFeed.find(({ entry }) => entry.event_type.startsWith('SETTLEMENT_'))?.entry ?? null

  async function run(intent: string, cap: string) {
    setRunFrom(entries.length)
    setRunning(true)
    setResult(null)
    setHold(null)
    setError(null)
    try {
      const response = await startPurchase({
        principal: PRINCIPAL,
        agentId: AGENT_ID,
        intent,
        maxAmount: { currency: 'XSGD', amount: cap },
        ttlSeconds: TTL_SECONDS,
      })
      setResult(response)
      if (response.hold && response.hold.outcome === 'PENDING') {
        setHold(response.hold)
        setModalOpen(true)
      }
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause))
    } finally {
      setRunning(false)
    }
  }

  async function resolve(decision: 'approve' | 'kill') {
    if (!hold) return
    setResolving(true)
    setError(null)
    try {
      const call = decision === 'approve' ? approveReview : killReview
      const response = await call(hold.charge_id, ACTOR)
      setResult(response)
      setHold(null)
      setModalOpen(false)
    } catch (cause) {
      // 409 means the hold lapsed or was already answered. That is an ordinary
      // outcome of a slow human, so it belongs in the modal, not in a console.
      setError(cause instanceof ApiError ? cause.message : String(cause))
    } finally {
      setResolving(false)
    }
  }

  return (
    <div className="mx-auto min-h-screen max-w-3xl px-5 py-10">
      <header className="flex items-baseline justify-between gap-4">
        <h1 className="display text-3xl">
          Trust<span className="text-brand">Rail</span>
        </h1>
        <StreamPill status={status} />
      </header>

      <p className="mt-2 max-w-xl text-sm text-charcoal">
        We do not trust the agent, we trust the rail. Delegate a budget, watch it be
        checked, and see what happens when the checks say no.
      </p>

      <div className="mt-8">
        <IntentBar busy={running} onSubmit={run} />
      </div>

      {error && !modalOpen && (
        <p className="mt-4 rounded-md bg-fail-bg px-4 py-3 text-sm font-semibold text-fail">
          {error}
        </p>
      )}

      {hold && !modalOpen && (
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="mt-4 w-full rounded-md bg-review-bg px-4 py-3 text-left text-sm font-semibold text-review"
        >
          One charge is still waiting on you — reopen
        </button>
      )}

      {runFrom !== null && (
        <section className="mt-10">
          <h2 className="mb-4 text-xs font-semibold tracking-widest text-charcoal uppercase">
            What the rail did
          </h2>
          <FlowTimeline steps={steps} />
        </section>
      )}

      {result && !modalOpen && (
        <div className="mt-4">
          <OutcomeCard result={result} settlement={settlementEntry} />
        </div>
      )}

      {runFrom === null && (
        <p className="mt-10 rounded-md border border-dashed border-hairline p-6 text-sm text-mute">
          Nothing running. Type an intent above — the mandate is minted before any product
          is chosen, so what you approve is a budget, not a basket.
        </p>
      )}

      {modalOpen && hold && (
        <ReviewModal
          hold={hold}
          busy={resolving}
          error={error}
          onApprove={() => resolve('approve')}
          onKill={() => resolve('kill')}
          onDismiss={() => setModalOpen(false)}
        />
      )}
    </div>
  )
}

function StreamPill({ status }: { status: 'connecting' | 'live' | 'offline' }) {
  const tone =
    status === 'live'
      ? 'bg-pass-bg text-pass'
      : status === 'connecting'
        ? 'bg-review-bg text-review'
        : 'bg-fail-bg text-fail'
  const label =
    status === 'live' ? 'feed live' : status === 'connecting' ? 'connecting' : 'feed offline'
  return (
    <span className={`rounded-full px-3 py-1 text-xs font-semibold ${tone}`}>{label}</span>
  )
}
