import { useState } from 'react'

import type { Step } from '../steps'
import type { AuditEntry, Charge, Verdict } from '../types'
import { KindTag, ReasonCode, StatusBadge } from './StatusBadge'

/**
 * Snowtrace. Hardcoded because settlement is XSGD on Avalanche C-Chain and that
 * is a track rule, not a deployment choice.
 */
const EXPLORER = 'https://snowtrace.io/tx/'
//: Global is safe with `String.match`, which returns every match and leaves no
//: lastIndex behind -- unlike `.test()`, which is stateful on a /g regex.
const TX_HASH = /0x[0-9a-fA-F]{64}/g

/** What the buyer asked for, so rows can be phrased in their terms. */
export interface RunContext {
  cap: string
  charge: Charge | null
}

function TxLink({ hash, label }: { hash: string; label?: string }) {
  return (
    <a
      href={`${EXPLORER}${hash}`}
      target="_blank"
      rel="noreferrer"
      className="font-mono text-xs text-brand underline underline-offset-2"
      title={hash}
    >
      {label ?? `${hash.slice(0, 10)}…${hash.slice(-6)}`}
    </a>
  )
}

/** The first transaction hash in a string, if it carries one. */
function hashIn(text: string): string | null {
  const match = text.match(TX_HASH)
  return match ? match[0] : null
}

/**
 * Plain language for one entry.
 *
 * The audit summaries stay precise because they are the record -- they carry
 * ISO timestamps, SKUs and raw hashes on purpose. None of that is what someone
 * watching needs, so presentation is decided here rather than by softening the
 * thing we would show a regulator.
 *
 * Returning null means the row's own structured rendering says it better.
 */
function describe(entry: AuditEntry, run: RunContext): string | null {
  switch (entry.event_type) {
    case 'MANDATE_MINTED':
      return `Spending limit set: up to ${run.cap} XSGD, for the next 10 minutes. No product chosen yet.`
    case 'CANDIDATE_SELECTED':
      // Once the purchase returns we have the charge itself and can drop the
      // SKU and merchant id, which mean nothing to a viewer.
      return run.charge
        ? `${run.charge.title} - ${run.charge.amount.amount} ${run.charge.amount.currency}`
        : entry.summary
    case 'MANDATE_BOUND':
      return 'You approved. The mandate is now locked to this seller and this basket.'
    case 'MANDATE_REVOKED':
      return hashIn(entry.summary)
        ? 'Recorded on the blockchain - the permission is publicly withdrawn.'
        : 'You rejected it. The permission is cancelled, so a retry cannot reuse it.'
    case 'MANDATE_CONSUMED':
      return 'Permission used up. It cannot be spent twice.'
    default:
      return null
  }
}

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
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
    return <span className={`mt-2 block size-3 rounded-full ${FILL[tone]}`} />
  }
  if (state === 'active') {
    const pulse = tone === 'neutral' ? 'bg-review' : FILL[tone]
    return (
      <span className="relative mt-2 block size-3">
        <span className={`absolute inset-0 animate-ping rounded-full opacity-60 ${pulse}`} />
        <span className={`absolute inset-0 rounded-full ${pulse}`} />
      </span>
    )
  }
  return (
    <span className={`mt-2 block size-3 rounded-full border-2 bg-canvas ${RING[tone]}`} />
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
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function EntryBody({ entry, run }: { entry: AuditEntry; run: RunContext }) {
  const plain = describe(entry, run)
  const hash = hashIn(entry.summary)

  return (
    <div className="mt-1">
      {plain && <p className="text-sm text-body">{plain}</p>}

      {/* Merchant- and LLM-supplied text where we have nothing better. Always
          text, never markup: this is the injection surface. */}
      {!plain && !entry.verdict && !entry.evaluation && (
        <p className="text-sm text-body">{entry.summary}</p>
      )}

      {hash && (
        <p className="mt-1 text-xs text-mute">
          On the blockchain: <TxLink hash={hash} />
        </p>
      )}

      {entry.evaluation && (
        <ul className="mt-1 space-y-1">
          {entry.evaluation.reasons.map((reason, i) => (
            <li key={i} className="text-sm text-body">
              - {reason}
            </li>
          ))}
        </ul>
      )}

      {entry.verdict && (
        <>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <StatusBadge decision={entry.verdict.decision} />
            {entry.verdict.reason_codes.map((code) => (
              <ReasonCode key={code} code={code} />
            ))}
          </div>
          {entry.verdict.failed_deterministically && (
            <p className="mt-2 text-sm font-semibold text-fail">
              This one is a fact, not a judgement call. Nobody can approve past it - a
              bigger purchase needs a new spending limit.
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
          className="mt-2 inline-flex h-10 items-center rounded-full bg-ink px-5 text-sm font-semibold text-on-dark"
        >
          View the payment on Snowtrace
        </a>
      )}
    </div>
  )
}

export function FlowTimeline({ steps, run }: { steps: Step[]; run: RunContext }) {
  return (
    <ol className="space-y-0" aria-live="polite" aria-relevant="additions">
      {steps.map((step, index) => {
        const last = index === steps.length - 1
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
                {/* A size above the body text, so the actions read as the
                    structure and the detail reads as detail. */}
                <h3 className="text-lg font-semibold text-ink">{step.title}</h3>
                {step.entry && (
                  <span className="font-mono text-xs text-ash">
                    {time(step.entry.occurred_at)}
                  </span>
                )}
                {step.state === 'skipped' && (
                  <span className="text-xs text-mute">- never happened</span>
                )}
                {step.state === 'queued' && (
                  <span className="text-xs font-semibold text-ink">- paying now</span>
                )}
                {waiting && <span className="text-xs font-semibold text-review">- waiting</span>}
              </div>

              {step.entry ? (
                <EntryBody entry={step.entry} run={run} />
              ) : (
                <p className={`mt-1 text-sm ${step.state === 'queued' ? 'text-body' : 'text-mute'}`}>
                  {step.state === 'queued'
                    ? 'Sent for payment. The contract checks the limit one last time before any money moves.'
                    : step.hint}
                </p>
              )}

              {step.reverification?.verdict && (
                <div className="mt-3 rounded-md border border-hairline bg-surface-bone/60 p-3">
                  <p className="text-sm font-semibold text-ink">Checked again after you approved</p>
                  <p className="mt-1 text-sm text-charcoal">
                    Approving does not skip the checks. The purchase went back through the
                    Verifier with the seller and the basket now locked in.
                  </p>
                </div>
              )}

              {step.notes.map((note) => {
                const text = describe(note, run)
                const noteHash = hashIn(note.summary)
                return (
                  <p key={note.event_id} className="mt-1 text-sm text-charcoal">
                    {text ?? note.summary}
                    {noteHash && (
                      <>
                        {' '}
                        <TxLink hash={noteHash} />
                      </>
                    )}
                  </p>
                )
              })}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
