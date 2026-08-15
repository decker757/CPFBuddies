import type { Money, PurchaseResponse, ReviewHold } from './types'

export const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

/**
 * An error the API answered with deliberately, carrying its status.
 *
 * A held charge that has expired comes back as 409, and that is an ordinary
 * outcome of a human being slower than a ten-minute window -- not a crash, and
 * worth telling the user in their own words rather than swallowing.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function send<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { 'content-type': 'application/json', ...init?.headers },
    })
  } catch {
    // fetch only rejects for transport failures, and by far the likeliest here
    // is that the API is not running at all.
    throw new ApiError(0, `Cannot reach the API at ${API_BASE}. Is it running?`)
  }
  if (!response.ok) {
    throw new ApiError(response.status, await describe(response))
  }
  return (await response.json()) as T
}

/** FastAPI spells errors two ways, and neither is the one this app throws. */
async function describe(response: Response): Promise<string> {
  try {
    const body = await response.json()
    if (typeof body?.detail === 'string') return body.detail
    if (typeof body?.detail?.[0]?.msg === 'string') return body.detail[0].msg
    if (typeof body?.detail === 'object') return JSON.stringify(body.detail)
    return String(body?.error ?? response.statusText)
  } catch {
    return response.statusText || `HTTP ${response.status}`
  }
}

export interface PurchaseInput {
  principal: string
  agentId: string
  intent: string
  maxAmount: Money
  ttlSeconds: number
}

/**
 * Run one purchase intent end to end.
 *
 * This blocks for the whole flow -- mint, shop, evaluate, verify, enqueue --
 * and answers with the final verdict, which is why the timeline is drawn from
 * the audit stream instead of from this response. Every verdict returns 200: a
 * FAIL is a decision this system made correctly, not a failed request.
 */
export function startPurchase(input: PurchaseInput): Promise<PurchaseResponse> {
  return send<PurchaseResponse>('/purchases', {
    method: 'POST',
    body: JSON.stringify({
      principal: input.principal,
      agent_id: input.agentId,
      intent: input.intent,
      max_amount: input.maxAmount,
      ttl_seconds: input.ttlSeconds,
    }),
  })
}

export function approveReview(chargeId: string, actor: string) {
  return send<PurchaseResponse>(`/reviews/${chargeId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ actor }),
  })
}

export function killReview(chargeId: string, actor: string) {
  return send<PurchaseResponse>(`/reviews/${chargeId}/kill`, {
    method: 'POST',
    body: JSON.stringify({ actor }),
  })
}

export function pendingReviews() {
  return send<ReviewHold[]>('/reviews')
}
