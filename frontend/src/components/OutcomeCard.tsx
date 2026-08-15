import type { AuditEntry, PurchaseResponse } from '../types'
import { ReasonCode, StatusBadge } from './StatusBadge'

const TONE = {
  PASS: 'border-pass/30 bg-pass-bg',
  REVIEW: 'border-review/30 bg-review-bg',
  FAIL: 'border-fail/30 bg-fail-bg',
} as const

/**
 * How it ended, in a sentence.
 *
 * The distinction this card is careful about: a PASS means the Verifier allowed
 * the charge and it went to the queue, which is *not* the same as money having
 * moved. Only a settlement entry says that, and offline there will never be one
 * -- so the card reports "queued" until the chain says otherwise rather than
 * congratulating anyone prematurely.
 */
export function OutcomeCard({
  result,
  settlement,
}: {
  result: PurchaseResponse
  settlement: AuditEntry | null
}) {
  const { verdict } = result
  const record = settlement?.settlement ?? null
  const settled = record?.outcome === 'SETTLED'

  return (
    <section className={`enter rounded-lg border p-5 ${TONE[verdict.decision]}`}>
      <div className="flex flex-wrap items-center gap-3">
        <StatusBadge decision={verdict.decision} />
        <h2 className="font-semibold text-ink">{headline(result, settled, record?.outcome)}</h2>
      </div>

      <p className="mt-2 text-sm text-body">{explain(result, settled, record?.outcome)}</p>

      {verdict.reason_codes.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {verdict.reason_codes.map((code) => (
            <ReasonCode key={code} code={code} />
          ))}
        </div>
      )}

      {verdict.failed_deterministically && (
        <p className="mt-3 text-sm font-semibold text-fail">
          This was a cryptographic or arithmetic fact, not a judgement call. There is no
          approval button for it — spending more would take a new mandate.
        </p>
      )}

      {record?.explorer_url && (
        <a
          href={record.explorer_url}
          target="_blank"
          rel="noreferrer"
          className="mt-4 inline-flex h-11 items-center rounded-full bg-ink px-6 font-semibold text-on-dark"
        >
          View on Snowtrace
        </a>
      )}

      {record && !record.explorer_url && record.detail && (
        <p className="mt-3 font-mono text-xs text-charcoal">{record.detail}</p>
      )}

      <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-1 font-mono text-xs text-charcoal sm:grid-cols-2">
        <Row label="mandate" value={result.mandate.signed.mandate.mandate_id} />
        <Row label="charge" value={result.charge.charge_id} />
        <Row label="amount" value={`${result.charge.amount.amount} ${result.charge.amount.currency}`} />
        <Row label="cap" value={`${result.mandate.signed.mandate.max_amount.amount} XSGD`} />
      </dl>
    </section>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2 overflow-hidden">
      <dt className="shrink-0 text-ash">{label}</dt>
      <dd className="truncate" title={value}>
        {value}
      </dd>
    </div>
  )
}

function headline(
  result: PurchaseResponse,
  settled: boolean,
  outcome: string | undefined,
): string {
  if (settled) return 'Paid. XSGD moved on Avalanche C-Chain.'
  if (outcome === 'REFUSED') return 'The rail refused it.'
  if (outcome === 'ERROR') return 'Settlement errored.'

  // How a hold ended outranks the verdict it started from. `kill_review` hands
  // back the original REVIEW verdict with nothing queued, which is
  // indistinguishable from a hold still waiting unless the outcome is read.
  switch (result.hold?.outcome) {
    case 'KILLED':
      return 'You killed it. The mandate is revoked.'
    case 'EXPIRED':
      return 'The hold expired. Nothing moved.'
  }

  switch (result.verdict.decision) {
    case 'PASS':
      return result.queued_message_id ? 'Approved and queued to settle.' : 'Approved.'
    case 'REVIEW':
      return result.queued_message_id ? 'Released by you, queued to settle.' : 'Waiting on you.'
    case 'FAIL':
      return 'Refused. Nothing moved.'
  }
}

function explain(
  result: PurchaseResponse,
  settled: boolean,
  outcome: string | undefined,
): string {
  if (settled) return 'The contract re-checked the cap, the merchant and the expiry, then transferred.'
  if (outcome === 'REFUSED')
    return 'The rail worked and declined — an onchain revert is the enforcement doing its job. Retrying cannot change the answer.'
  if (outcome === 'ERROR') return 'The rail itself failed rather than declining. This one can be retried.'

  switch (result.hold?.outcome) {
    case 'KILLED':
      return 'Revoking rather than just closing the charge is the point: a retry cannot quietly pick something else under authority you have withdrawn.'
    case 'EXPIRED':
      return 'A review nobody answers inside the mandate window fails. There is no pending queue for it to sit in.'
  }

  if (result.verdict.decision === 'FAIL')
    return 'The Verifier rejected this charge before anything reached the chain.'
  if (result.queued_message_id)
    return 'The charge is on the settlement queue. Money moves when the worker runs — offline, nothing will.'
  return 'Held for a human. Nothing settles until someone answers.'
}
