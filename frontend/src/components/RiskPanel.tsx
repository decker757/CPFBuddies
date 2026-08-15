import type { EvaluatorOutput } from '../types'

/**
 * Risk bands, matching the Verifier's own config defaults: 1-3 settles, 4-7
 * asks a human, 8-10 refuses. Stated in the same place as the colour so the two
 * cannot drift apart and imply an outcome the server would disagree with.
 */
export function band(score: number) {
  if (score <= 3) return { label: 'Looks fine', tone: 'pass' } as const
  if (score <= 7) return { label: 'Needs a human', tone: 'review' } as const
  return { label: 'Refused', tone: 'fail' } as const
}

const SHELL = {
  pass: 'border-pass/30 bg-pass-bg',
  review: 'border-review/30 bg-review-bg',
  fail: 'border-fail/30 bg-fail-bg',
} as const

const INK = {
  pass: 'text-pass',
  review: 'text-review',
  fail: 'text-fail',
} as const

/**
 * The headline number.
 *
 * The risk score is the one figure a viewer should be able to read from across
 * a room, and it was previously a small chip inside a timeline row competing
 * with four other things. It sits above the rail now and says nothing else --
 * the reasons behind it stay in the Evaluator's own row, so the same finding is
 * not presented twice in two shapes.
 */
export function RiskPanel({ evaluation }: { evaluation: EvaluatorOutput | null }) {
  if (!evaluation) return null
  const { label, tone } = band(evaluation.risk_score)

  return (
    <section className={`enter flex items-center gap-5 rounded-lg border p-5 ${SHELL[tone]}`}>
      <div className="flex items-baseline gap-1">
        <span className={`display text-6xl ${INK[tone]}`}>{evaluation.risk_score}</span>
        <span className={`text-xl font-semibold ${INK[tone]} opacity-60`}>/10</span>
      </div>
      <div>
        <p className={`text-lg font-semibold ${INK[tone]}`}>{label}</p>
        <p className="text-sm text-charcoal">
          Risk score from the Evaluator Agent. It is evidence for the Verifier, never the
          decision itself.
        </p>
      </div>
    </section>
  )
}
