import { useEffect, useRef, useState } from 'react'

import type { ReviewHold } from '../types'
import { band } from './RiskPanel'

const RISK_SHELL = {
  pass: 'border-pass/30 bg-pass-bg',
  review: 'border-review/30 bg-review-bg',
  fail: 'border-fail/30 bg-fail-bg',
} as const

const RISK_INK = { pass: 'text-pass', review: 'text-review', fail: 'text-fail' } as const

/** Time left before the hold lapses, or null once it has. */
function useCountdown(deadline: string): number | null {
  const [remaining, setRemaining] = useState(() => msLeft(deadline))
  useEffect(() => {
    const timer = setInterval(() => setRemaining(msLeft(deadline)), 500)
    return () => clearInterval(timer)
  }, [deadline])
  return remaining > 0 ? remaining : null
}

function msLeft(deadline: string): number {
  return new Date(deadline).getTime() - Date.now()
}

function clock(ms: number): string {
  const total = Math.floor(ms / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

const FLAG_LABELS: Record<string, string> = {
  intent_match: 'Matches your intent',
  injection_suspected: 'Injection suspected',
  price_far_below_market: 'Price far below market',
  seller_is_new: 'Seller is new',
}

/**
 * The pause. A charge is held and nothing moves until a person answers.
 *
 * Two rules this component exists to keep:
 *
 * 1. **`hold.approvable` gates the approve button.** It is false when a fact
 *    rejected the charge -- a bad signature, an over-cap amount -- and offering
 *    a button there would teach people to click past facts, which is the one
 *    thing that would destroy what this system claims. The server decides;
 *    this does not re-derive it.
 * 2. **Closing is not deciding.** Dismissing leaves the hold pending on the
 *    server, so the caller keeps showing it rather than pretending it is gone.
 */
export function ReviewModal({
  hold,
  busy,
  error,
  onApprove,
  onKill,
  onDismiss,
}: {
  hold: ReviewHold
  busy: boolean
  error: string | null
  onApprove: () => void
  onKill: () => void
  onDismiss: () => void
}) {
  const remaining = useCountdown(hold.deadline)
  const killRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    // Focus the safe choice, not the spending one.
    killRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onDismiss()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onDismiss])

  const expired = remaining === null
  const { charge, evaluation } = hold
  const score = evaluation.evaluation.risk_score
  const { tone } = band(score)

  // One list. The Evaluator's own sentences say it best; the flags only add
  // anything when it gave no reasons at all. `intent_match` is the single flag
  // where true is the good news, which is exactly the sort of inversion that
  // renders a reassuring "Matches your intent" next to a refusal if missed.
  const flagged = Object.entries(evaluation.evaluation.flags)
    .filter(([name, value]) => (name === 'intent_match' ? !value : value))
    .map(([name]) => FLAG_LABELS[name] ?? name)
  const concerns = evaluation.evaluation.reasons.length
    ? evaluation.evaluation.reasons
    : flagged

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/40 p-4 sm:items-center">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="review-title"
        className="enter max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-lg bg-surface-card p-6 shadow-xl"
      >
        <div className="flex items-start justify-between gap-4">
          <h2 id="review-title" className="display text-2xl">
            Approve this purchase?
          </h2>
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Close and decide later"
            className="rounded-full px-2 py-1 text-mute hover:bg-surface-bone"
          >
            ✕
          </button>
        </div>

        {/* Price and risk carry equal weight, because they are the two numbers
            the decision actually turns on. */}
        <div className="mt-4 grid grid-cols-2 gap-3">
          <div className="rounded-md border border-hairline p-4">
            <p className="text-xs font-semibold tracking-wide text-charcoal uppercase">
              Price
            </p>
            <p className="display mt-1 text-3xl">
              {charge.amount.amount}
              <span className="ml-1 text-base text-charcoal">{charge.amount.currency}</span>
            </p>
          </div>
          <div className={`rounded-md border p-4 ${RISK_SHELL[tone]}`}>
            <p className="text-xs font-semibold tracking-wide text-charcoal uppercase">
              Risk
            </p>
            <p className={`display mt-1 text-3xl ${RISK_INK[tone]}`}>
              {score}
              <span className="ml-1 text-base opacity-60">/10</span>
            </p>
          </div>
        </div>

        {/* Merchant-supplied. Text only -- this is the injection surface. */}
        <p className="mt-4 font-semibold text-ink">{charge.title}</p>

        {/* One list, once. The same finding used to appear as a chip, a
            sentence, a flag and a reason code, which reads as four problems
            rather than one. */}
        <ul className="mt-3 space-y-1.5">
          {concerns.map((concern, i) => (
            <li key={i} className="text-sm text-body">
              — {concern}
            </li>
          ))}
        </ul>

        <p className="mt-4 text-sm text-charcoal">
          {expired ? (
            <span className="font-semibold text-fail">
              This hold has expired. An unanswered review fails; nothing moved.
            </span>
          ) : (
            <>
              Expires in{' '}
              <span className="font-mono font-semibold text-ink">{clock(remaining)}</span>. A
              review nobody answers becomes a FAIL.
            </>
          )}
        </p>

        {error && <p className="mt-3 text-sm font-semibold text-fail">{error}</p>}

        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            ref={killRef}
            type="button"
            onClick={onKill}
            disabled={busy || expired}
            className="h-11 rounded-full border border-hairline-strong px-6 font-semibold text-ink disabled:opacity-40"
          >
            Kill it
          </button>

          {hold.approvable ? (
            <button
              type="button"
              onClick={onApprove}
              disabled={busy || expired}
              className="h-11 rounded-full bg-ink px-6 font-semibold text-on-dark disabled:opacity-40"
            >
              {busy ? 'Working…' : 'Approve and settle'}
            </button>
          ) : (
            /*
              Not a disabled button -- no button at all. A deterministic failure
              is not something a person is allowed to be asked about, and a
              greyed-out control still implies the answer is somewhere.
            */
            <p className="self-center text-sm font-semibold text-fail">
              A fact rejected this charge. It cannot be approved.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
