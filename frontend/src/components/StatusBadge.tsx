import type { Decision } from '../types'

const TONE: Record<Decision, string> = {
  PASS: 'bg-pass-bg text-pass',
  REVIEW: 'bg-review-bg text-review',
  FAIL: 'bg-fail-bg text-fail',
}

/**
 * PASS / REVIEW / FAIL.
 *
 * Colour is never the only signal -- the word is always present, because a
 * projector and a colour-blind viewer will each lose one of the two.
 */
export function StatusBadge({
  decision,
  size = 'md',
}: {
  decision: Decision
  size?: 'sm' | 'md'
}) {
  const scale = size === 'sm' ? 'text-xs px-2 py-0.5' : 'text-sm px-3 py-1'
  return (
    <span
      className={`inline-flex items-center rounded-full font-semibold tracking-wide ${TONE[decision]} ${scale}`}
    >
      {decision}
    </span>
  )
}

/** A reason code. Stable server-side strings, shown verbatim. */
export function ReasonCode({ code }: { code: string }) {
  return (
    <code className="rounded-xs bg-surface-bone px-1.5 py-0.5 font-mono text-xs text-charcoal">
      {code}
    </code>
  )
}

/**
 * DETERMINISTIC or JUDGEMENT.
 *
 * The distinction the whole verdict model rests on, so it is labelled rather
 * than left to be inferred from which check happened to fail.
 */
export function KindTag({ kind }: { kind: 'DETERMINISTIC' | 'JUDGEMENT' }) {
  const deterministic = kind === 'DETERMINISTIC'
  return (
    <span
      className={`rounded-xs px-1.5 py-0.5 font-mono text-[10px] tracking-wide ${
        deterministic ? 'bg-ink text-on-dark' : 'bg-surface-bone text-charcoal'
      }`}
      title={
        deterministic
          ? 'A fact: signature, expiry, cap, nonce, counterparty. No override.'
          : 'A threshold or an LLM finding. Tunable in config.'
      }
    >
      {kind}
    </span>
  )
}
