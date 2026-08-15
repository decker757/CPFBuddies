import type { AuditEntry, FeedEntry } from './types'

/**
 * The audit trail, abstracted into the beats a person actually follows.
 *
 * The raw feed is a ledger and reads like one. What a viewer needs to see is
 * the rail working: a budget signed, an agent shopping, a judgement made, a
 * decision taken, money moving or not. Each step lights up when its entry
 * arrives, so a purchase that is still running looks like it is running.
 *
 * Nothing here interprets a verdict -- the decision, the reason codes and
 * whether a failure was deterministic all come from the server. This only
 * decides what to draw and in which order.
 */

export type StepKey =
  | 'mandate'
  | 'candidate'
  | 'evaluation'
  | 'verdict'
  | 'decision'
  | 'settlement'

export type StepState = 'pending' | 'active' | 'done' | 'skipped' | 'queued'

/**
 * What a step's marker should say at a glance.
 *
 * Only steps that carry an outcome get a colour. Minting a mandate and shopping
 * are things that happened, not things that went well or badly, and tinting
 * them would spend the reader's attention on rows that are never the answer --
 * and leave the one red dot competing with five others for notice.
 */
export type StepTone = 'neutral' | 'pass' | 'review' | 'fail'

export interface Step {
  key: StepKey
  title: string
  hint: string
  state: StepState
  tone: StepTone
  entry?: AuditEntry
  /**
   * The second verdict, issued when an approval re-entered the Verifier.
   *
   * Carried separately because it is the whole point of the approve path:
   * CLAUDE.md is explicit that approving "does not skip verification -- it
   * supplies more to verify". Folding it into the first verdict would hide the
   * re-run, which is the thing worth showing.
   */
  reverification?: AuditEntry
  /** Lifecycle entries folded under this step rather than given rows of their own. */
  notes: AuditEntry[]
}

interface Context {
  running: boolean
  queued: boolean
}

//: Named for who acts, because who acted is the point. The Browser Agent and
//: the Evaluator Agent are agents and are assumed compromisable; the Verifier
//: Service is not one, and blurring that would undo the claim the whole system
//: makes -- that the checking is done by something the agents cannot influence.
const ORDER: { key: StepKey; title: string; hint: string }[] = [
  {
    key: 'mandate',
    title: 'Spending limit set',
    hint: 'Your budget, signed and recorded. No product chosen yet.',
  },
  {
    key: 'candidate',
    title: 'Browser Agent chose a product',
    hint: 'Shopping now. Nothing later in the chain takes its word for anything.',
  },
  {
    key: 'evaluation',
    title: 'Evaluator Agent scored it',
    hint: 'Checking the product against what you asked for.',
  },
  {
    key: 'verdict',
    title: 'Verifier Service decided',
    hint: 'The checks that cannot be argued with run first.',
  },
  {
    key: 'decision',
    title: 'Your decision',
    hint: 'Paused for you. Nothing moves until you answer.',
  },
  {
    key: 'settlement',
    title: 'Payment',
    hint: 'The contract checks the limit one last time, or refuses.',
  },
]

const STEP_FOR_EVENT: Partial<Record<AuditEntry['event_type'], StepKey>> = {
  MANDATE_MINTED: 'mandate',
  CANDIDATE_SELECTED: 'candidate',
  EVALUATION_COMPLETE: 'evaluation',
  SETTLEMENT_SETTLED: 'settlement',
  SETTLEMENT_REFUSED: 'settlement',
  SETTLEMENT_FAILED: 'settlement',
}

/** How a human answered a hold. Either one is a decision worth its own row. */
const DECISION_EVENTS = new Set<AuditEntry['event_type']>([
  'MANDATE_BOUND',
  'MANDATE_REVOKED',
])

function toneFor(
  key: StepKey,
  state: StepState,
  entry: AuditEntry | undefined,
): StepTone {
  if (key === 'verdict') {
    // The decision itself, straight from the server.
    return entry?.verdict ? (TONE_FOR_DECISION[entry.verdict.decision] ?? 'neutral') : 'neutral'
  }

  if (key === 'decision') {
    if (!entry) return 'review' // still waiting on a person
    // Approving is not a pass. The finding stayed REVIEW and a named human
    // released it anyway, so the marker should not go green and imply the
    // charge came back clean.
    return entry.event_type === 'MANDATE_REVOKED' ? 'fail' : 'review'
  }

  if (key === 'settlement') {
    if (entry) {
      return entry.event_type === 'SETTLEMENT_SETTLED' ? 'pass' : 'fail'
    }
    if (state === 'skipped') return 'fail' // rejected or revoked: money never moved
    if (state === 'queued') return 'review' // waiting on a worker that may never run
    return 'neutral'
  }

  return 'neutral'
}

const TONE_FOR_DECISION: Record<string, StepTone> = {
  PASS: 'pass',
  REVIEW: 'review',
  FAIL: 'fail',
}

export function buildSteps(feed: FeedEntry[], context: Context): Step[] {
  const found = new Map<StepKey, AuditEntry>()
  const verdicts: AuditEntry[] = []
  const notes: AuditEntry[] = []
  const decisionNotes: AuditEntry[] = []
  let revoked = false

  for (const { entry } of feed) {
    const key = STEP_FOR_EVENT[entry.event_type]
    if (key && !found.has(key)) found.set(key, entry)

    if (entry.event_type === 'VERDICT_ISSUED') verdicts.push(entry)

    if (DECISION_EVENTS.has(entry.event_type)) {
      if (found.has('decision')) {
        // A decision produces more than one entry: revoking writes the local
        // record first and then, separately, the onchain revocation with its
        // transaction hash. Dropping the extras would throw away the only
        // proof that the ledger agrees.
        decisionNotes.push(entry)
      } else {
        found.set('decision', entry)
      }
    }
    if (entry.event_type === 'MANDATE_REVOKED') revoked = true
    if (entry.event_type === 'MANDATE_CONSUMED') notes.push(entry)
  }

  // The first verdict is the machine's answer. A second one only exists because
  // a human approved and the charge went back through the Verifier.
  const [firstVerdict, ...laterVerdicts] = verdicts
  if (firstVerdict) found.set('verdict', firstVerdict)

  const decision = firstVerdict?.verdict?.decision ?? null
  const awaitingHuman = decision === 'REVIEW' && !found.has('decision')

  // A FAIL settles nothing, and neither does a mandate a human killed. Leaving
  // those pending would show a spinner for something that is never coming.
  const settlementUnreachable =
    decision === 'FAIL' || (revoked && !found.has('settlement'))

  let activeAssigned = false
  const steps = ORDER.map(({ key, title, hint }): Step => {
    const entry = found.get(key)
    let state: StepState = 'pending'

    if (entry) {
      state = 'done'
    } else if (key === 'settlement' && settlementUnreachable) {
      state = 'skipped'
    } else if (key === 'settlement' && context.queued) {
      // Queued is not settled, and saying so is the difference between "we
      // paid" and "the worker has not run". Offline it never will.
      state = 'queued'
    } else if (key === 'decision' && awaitingHuman) {
      state = 'active'
    } else if (context.running && !activeAssigned) {
      state = 'active'
      activeAssigned = true
    }

    return {
      key,
      title,
      hint,
      state,
      tone: toneFor(key, state, entry),
      entry,
      reverification: key === 'decision' ? laterVerdicts[0] : undefined,
      notes: key === 'settlement' ? notes : key === 'decision' ? decisionNotes : [],
    }
  })

  // Only show the human step when there is or was a human in the loop. A clean
  // PASS never paused, and a permanently grey "Your decision" row would imply
  // it was waiting on something.
  return steps.filter(
    (step) => step.key !== 'decision' || step.entry !== undefined || awaitingHuman,
  )
}
