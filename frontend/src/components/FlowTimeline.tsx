import { useState } from 'react'

import type { Step } from '../steps'
import type { AuditEntry, Verdict } from '../types'
import { KindTag, ReasonCode, RiskChip, StatusBadge } from './StatusBadge'

/**
 * Snowtrace. Hardcoded because settlement is XSGD on Avalanche C-Chain and
 * that is a track rule, not a deployment choice.
 */
const EXPLORER = 'https://snowtrace.io/tx/'
//: Global, for splitting. `.test()` is stateful on a /g regex and would return
//: alternating answers for the same input, so the check below uses its own.
const TX_HASH_SPLIT = /(0x[0-9a-fA-F]{64})/g
const IS_TX_HASH = /^0x[0-9a-fA-F]{64}$/

/**
 * Render a summary, turning any transaction hash inside it into a link.
 *
 * The mint and revoke hashes arrive inside prose -- "registered onchain in
 * 0x…" -- because the Mandate Service talks to a `MandateRegistrar` port and
 * deliberately knows nothing about chains, so it cannot build an explorer URL
 * itself. Recognising one here costs a regex and turns a string nobody can use
 * into the thing the demo ends on.
 *
 * Still text, never markup: the same summaries carry merchant-supplied titles.
 */
function Summary({ text }: { text: string }) {
  return (
    <>
      {text.split(TX_HASH_SPLIT).map((part, i) =>
        IS_TX_HASH.test(part) ? (
          <a
            key={i}
            href={`${EXPLORER}${part}`}
            target="_blank"
            rel="noreferrer"
            className="font-mono text-brand underline underline-offset-2"
            title={part}
          >
            {part.slice(0, 10)}…{part.slice(-6)}
          </a>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  )
}

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

/** Solid means it happened; a ring means it did not, and the tone says how that reads. */
const FILL: Record<Step['tone'], string> = {
  neutral: 'bg-ink',
  pass: 'bg-pass',
  review: 'bg-review',
  fail: 'bg-fail',
}
const RING: Record<Step['tone'], string> = {
  neutral: 'border-stone',
  pass: 'border-pass',
  review: 'border-review',
  fail: 'border-fail',
}

function Dot({ state, tone }: { state: Step['state']; tone: Step['tone'] }) {
  if (state === 'done') {
    return <span className={`mt-1.5 block size-3 rounded-full ${FILL[tone]}`} />
  }
  if (state === 'active') {
    // Amber rather than the brand orange: waiting is a status, and the accent
    // colour is reserved for the wordmark so it stays a stamp.
    const pulse = tone === 'neutral' ? 'bg-review' : FILL[tone]
    return (
      <span className="relative mt-1.5 block size-3">
        <span className={`absolute inset-0 animate-ping rounded-full opacity-60 ${pulse}`} />
        <span className={`absolute inset-0 rounded-full ${pulse}`} />
      </span>
    )
  }
  // Never happened. A hollow marker keeps that distinct from a step that ran
  // and failed, while the colour still says whether it is bad news.
  return (
    <span
      className={`mt-1.5 block size-3 rounded-full border-2 bg-canvas ${RING[tone]}`}
    />
  )
}

/** The full check trace, hidden until asked for. It is long and it is the proof. */
function CheckTrace({ verdict }: { verdict: Verdict }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="rounded-full px-2 py-1 text-xs font-semibold text-charcoal hover:bg-surface-bone"
      >
        {open ? 'Hide' : 'Show'} all {verdict.checks.length} checks
      </button>
      {open && (
        <ul className="mt-2 space-y-1 border-l border-hairline pl-3">
          {verdict.checks.map((check) => (
            <li key={check.name} className="flex flex-wrap items-center gap-2 text-xs">
              <StatusBadge decision={check.decision} size="sm" />
              <KindTag kind={check.kind} />
              <span className="font-mono text-charcoal">{check.name}</span>
              {check.detail && <span className="text-mute">— {check.detail}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function EntryBody({ entry }: { entry: AuditEntry }) {
  return (
    <div className="mt-1">
      {/* Merchant- and LLM-supplied text. Rendered as text, never as markup. */}
      <p className="text-sm text-body">
        <Summary text={entry.summary} />
      </p>

      {entry.evaluation && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <RiskChip score={entry.evaluation.risk_score} />
          {entry.evaluation.reasons.map((reason, i) => (
            <span
              key={i}
              className="rounded-full bg-surface-bone px-2 py-0.5 text-xs text-charcoal"
            >
              {reason}
            </span>
          ))}
        </div>
      )}

      {entry.verdict && (
        <>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <StatusBadge decision={entry.verdict.decision} />
            {entry.verdict.reason_codes.map((code) => (
              <ReasonCode key={code} code={code} />
            ))}
          </div>
          {entry.verdict.failed_deterministically && (
            <p className="mt-2 text-xs font-semibold text-fail">
              This was a fact, not a threshold. No one may override it.
            </p>
          )}
          <CheckTrace verdict={entry.verdict} />
        </>
      )}

      {entry.settlement?.explorer_url && (
        <a
          href={entry.settlement.explorer_url}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-block font-mono text-xs text-brand underline underline-offset-2"
        >
          {entry.settlement.reference?.slice(0, 18)}… — open on Snowtrace
        </a>
      )}
    </div>
  )
}

export function FlowTimeline({ steps }: { steps: Step[] }) {
  return (
    <ol
      className="space-y-0"
      // The feed updates without the user acting, so it has to announce itself.
      aria-live="polite"
      aria-relevant="additions"
    >
      {steps.map((step, index) => {
        const last = index === steps.length - 1
        // Dim only what genuinely has not happened yet. A settlement that was
        // never reached because the charge was rejected is the end of the
        // story, not a blank row, so it keeps full contrast.
        const dim =
          (step.state === 'pending' || step.state === 'skipped') && step.tone === 'neutral'
        const waiting = step.state === 'active' && !step.entry
        return (
          <li key={step.key} className="flex gap-3">
            <div className="flex flex-col items-center">
              <Dot state={step.state} tone={step.tone} />
              {!last && <span className="w-px grow bg-hairline" />}
            </div>

            <div className={`flex-1 pb-6 ${dim ? 'opacity-45' : 'enter'}`}>
              <div className="flex flex-wrap items-baseline gap-x-2">
                <h3 className="font-semibold text-ink">{step.title}</h3>
                {step.entry && (
                  <span className="font-mono text-xs text-ash">
                    {time(step.entry.occurred_at)} · {step.entry.actor}
                  </span>
                )}
                {step.state === 'skipped' && (
                  <span className="text-xs text-mute">— not reached</span>
                )}
                {step.state === 'queued' && (
                  <span className="text-xs font-semibold text-ink">— queued, not settled</span>
                )}
                {waiting && <span className="text-xs font-semibold text-review">— waiting</span>}
              </div>

              {step.entry ? (
                <EntryBody entry={step.entry} />
              ) : (
                <p className={`mt-1 text-sm ${step.state === 'queued' ? 'text-body' : 'text-mute'}`}>
                  {step.state === 'queued'
                    ? 'On the settlement queue. Money moves when the worker runs — offline, nothing will.'
                    : step.hint}
                </p>
              )}

              {/* The re-run after an approval. Showing it is the point: approving
                  supplies more to verify, it does not skip verification. */}
              {step.reverification?.verdict && (
                <div className="mt-3 rounded-md border border-hairline bg-surface-bone/60 p-3">
                  <p className="text-xs font-semibold text-ink">
                    Re-verified after your approval
                  </p>
                  <p className="mt-1 text-xs text-charcoal">
                    The mandate was bound to this merchant and this basket, then sent back
                    through the Verifier. An expiry or a revocation in the meantime would have
                    been caught here.
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <StatusBadge decision={step.reverification.verdict.decision} size="sm" />
                    <span className="text-xs text-mute">
                      still {step.reverification.verdict.decision} — approving records who
                      released it, it does not rewrite the finding
                    </span>
                  </div>
                </div>
              )}

              {/* The summaries already read as sentences ("revoked: rejected
                  during review"), so naming the event type again just stutters.
                  The actor is the part that adds something: who did it. */}
              {step.notes.map((note) => (
                <p key={note.event_id} className="mt-1 text-xs text-mute">
                  <Summary text={note.summary} />{' '}
                  <span className="text-stone">· {note.actor}</span>
                </p>
              ))}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
