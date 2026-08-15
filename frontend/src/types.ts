/**
 * The wire contract, in TypeScript.
 *
 * Mirrors `contracts/schemas/` -- regenerate those with
 * `python -m trustrail.contracts.export` and reconcile here if they move.
 *
 * Every string that came from a merchant or from the Evaluator is attacker
 * controlled: titles, summaries and reasons all originate in a listing payload.
 * They are rendered as text and never as markup. There is no
 * `dangerouslySetInnerHTML` in this app, and the injection demo is precisely
 * the reason not to add one.
 */

export type Decision = 'PASS' | 'REVIEW' | 'FAIL'
export type CheckKind = 'DETERMINISTIC' | 'JUDGEMENT'
export type ReviewOutcome = 'PENDING' | 'APPROVED' | 'KILLED' | 'EXPIRED'
export type SettlementOutcome = 'SETTLED' | 'REFUSED' | 'ERROR'

export type AuditEventType =
  | 'MANDATE_MINTED'
  | 'MANDATE_BOUND'
  | 'MANDATE_REVOKED'
  | 'MANDATE_CONSUMED'
  | 'KILL_SWITCH_SET'
  | 'CANDIDATE_SELECTED'
  | 'EVALUATION_COMPLETE'
  | 'VERDICT_ISSUED'
  | 'SETTLEMENT_SETTLED'
  | 'SETTLEMENT_REFUSED'
  | 'SETTLEMENT_FAILED'

export interface Money {
  currency: string
  amount: string
}

export interface CheckResult {
  name: string
  kind: CheckKind
  decision: Decision
  reason: string | null
  detail: string | null
}

export interface Verdict {
  decision: Decision
  reason_codes: string[]
  checks: CheckResult[]
  mandate_id: string
  charge_id: string
  risk_score: number
  evaluated_at: string
  verifier_version: string
  config_version: string
  /**
   * A fact rejected this charge, not a threshold -- so no override button may
   * be rendered, ever. Computed server-side and emitted but never accepted;
   * do not re-derive it from `checks` here, or the rule lives in two languages
   * and they drift.
   */
  failed_deterministically: boolean
}

export interface Charge {
  charge_id: string
  mandate_id: string
  merchant_id: string
  payout_address: string
  amount: Money
  basket_hash: string
  quote_id: string
  sku: string
  title: string
  quantity: number
}

export interface EvaluatorFlags {
  intent_match: boolean
  injection_suspected: boolean
  price_far_below_market: boolean
  seller_is_new: boolean
}

export interface EvaluatorOutput {
  evaluator_id: string
  subject: { mandate_id: string; basket_hash: string; amount: Money }
  risk_score: number
  flags: EvaluatorFlags
  reasons: string[]
}

export interface SignedEvaluatorOutput {
  evaluation: EvaluatorOutput
  digest: string
  signature: string
}

export interface Mandate {
  mandate_id: string
  principal: string
  agent_id: string
  max_amount: Money
  expires_at: string
  intent: string
  merchant_address: string | null
  basket_hash: string | null
  nonce: string
}

export interface MandateRecord {
  signed: { mandate: Mandate; digest: string; signature: string }
  status: string
  created_at: string
  updated_at: string
}

export interface ReviewHold {
  charge_id: string
  mandate_id: string
  verdict: Verdict
  charge: Charge
  evaluation: SignedEvaluatorOutput
  outcome: ReviewOutcome
  held_at: string
  deadline: string
  resolved_by: string | null
  /** Whether a human may be asked at all. False for a deterministic failure. */
  approvable: boolean
}

export interface SettlementRecord {
  rail: string
  outcome: SettlementOutcome
  reference: string | null
  explorer_url: string | null
  reason_code: string | null
  detail: string | null
}

export interface AuditEntry {
  event_id: string
  mandate_id: string
  event_type: AuditEventType
  occurred_at: string
  actor: string
  summary: string
  verdict: Verdict | null
  evaluation: EvaluatorOutput | null
  settlement: SettlementRecord | null
}

export interface PurchaseResponse {
  verdict: Verdict
  charge: Charge
  mandate: MandateRecord
  hold: ReviewHold | null
  queued_message_id: string | null
}

/** An audit entry plus the feed position it arrived at, used to de-duplicate. */
export interface FeedEntry {
  cursor: number
  entry: AuditEntry
}
