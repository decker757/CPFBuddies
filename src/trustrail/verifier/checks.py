"""The individual checks, in the order they run.

Each check is a plain function: given the context, return a `Rejection` or
`None`. It reads no clock, opens no connection, and touches no state. That
uniformity is what lets the pipeline in `service.py` stay six lines long and
lets each check be tested in isolation.

The two lists at the bottom of this file are the policy. Deterministic checks
run first and short-circuit — no point scoring the risk of a charge whose
mandate signature is forged — and every one of them is a fact: a signature
verifies or it does not, an amount is over the cap or it is not. Judgement
checks run only once every fact has passed, and they collect rather than
short-circuit, because a human staring at a REVIEW wants all the reasons at
once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property

from trustrail.models.charge import Charge
from trustrail.models.evaluation import EvaluatorOutput
from trustrail.models.mandate import Mandate, MandateStatus
from trustrail.models.primitives import to_bytes
from trustrail.models.registry import AgentRole, MerchantRecord
from trustrail.models.verdict import Decision, ReasonCode
from trustrail.models.verification import VerificationRequest
from trustrail.signing.crypto import signed_by
from trustrail.signing.eip712 import mandate_digest
from trustrail.signing.evidence import evaluation_digest
from trustrail.verifier.config import VerifierConfig


@dataclass(frozen=True, slots=True)
class Rejection:
    """Why a check refused, and how strongly.

    Deterministic checks leave `decision` at FAIL and the pipeline holds them to
    it. Judgement checks say REVIEW when a human should look.
    """

    reason: ReasonCode
    detail: str
    decision: Decision = Decision.FAIL


class VerificationContext:
    """The request, the config, and the values several checks share."""

    def __init__(self, request: VerificationRequest, config: VerifierConfig) -> None:
        self.request = request
        self.config = config

    @cached_property
    def recomputed_digest(self) -> str:
        """The digest as derived from the mandate's actual contents.

        Every signature check uses this, never the digest the caller supplied.
        A tampered `max_amount` changes this value, so the issuer's signature
        stops verifying — which is the whole reason the mandate is signed.
        """
        return mandate_digest(self.mandate, self.config.domain)

    @property
    def mandate(self) -> Mandate:
        return self.request.signed_mandate.mandate

    @property
    def charge(self) -> Charge:
        return self.request.charge

    @property
    def evaluation(self) -> EvaluatorOutput:
        return self.request.evaluation.evaluation

    @property
    def merchant(self) -> MerchantRecord | None:
        return self.request.merchant


# --------------------------------------------------------------------------
# Deterministic: is this mandate authentic and still alive?
# --------------------------------------------------------------------------


def check_mandate_digest(ctx: VerificationContext) -> Rejection | None:
    if ctx.request.signed_mandate.digest != ctx.recomputed_digest:
        return Rejection(
            ReasonCode.MANDATE_DIGEST_MISMATCH,
            "the supplied digest is not the digest of the mandate's contents",
        )
    return None


def check_issuer_signature(ctx: VerificationContext) -> Rejection | None:
    if not signed_by(
        to_bytes(ctx.recomputed_digest),
        ctx.request.signed_mandate.signature,
        ctx.config.issuer_address,
    ):
        return Rejection(
            ReasonCode.MANDATE_SIGNATURE_INVALID,
            "signature does not recover to the registered issuer address",
        )
    return None


def check_charge_targets_mandate(ctx: VerificationContext) -> Rejection | None:
    if ctx.charge.mandate_id != ctx.mandate.mandate_id:
        return Rejection(
            ReasonCode.CHARGE_MANDATE_MISMATCH,
            f"charge names mandate {ctx.charge.mandate_id}, "
            f"but the mandate presented is {ctx.mandate.mandate_id}",
        )
    return None


def check_not_expired(ctx: VerificationContext) -> Rejection | None:
    # Skew is applied in the safe direction only. Treating the mandate as
    # expiring slightly early can reject a still-valid charge, which costs a
    # retry; treating it as expiring late would let an expired mandate spend.
    effective_now = ctx.request.now + timedelta(seconds=ctx.config.clock_skew_seconds)
    if effective_now > ctx.mandate.expires_at:
        return Rejection(
            ReasonCode.MANDATE_EXPIRED,
            f"mandate expired at {ctx.mandate.expires_at.isoformat()}",
        )
    return None


def check_not_revoked(ctx: VerificationContext) -> Rejection | None:
    if ctx.request.mandate_state.status is MandateStatus.REVOKED:
        return Rejection(ReasonCode.MANDATE_REVOKED, "mandate has been revoked")
    return None


def check_not_already_consumed(ctx: VerificationContext) -> Rejection | None:
    if ctx.request.mandate_state.status is MandateStatus.CONSUMED:
        return Rejection(
            ReasonCode.MANDATE_ALREADY_CONSUMED,
            "mandate has already been spent; mandates are one-time",
        )
    return None


def check_kill_switch(ctx: VerificationContext) -> Rejection | None:
    if ctx.request.kill_switch_active:
        return Rejection(
            ReasonCode.KILL_SWITCH_ACTIVE,
            "settlement is halted for this principal",
        )
    return None


def check_nonce_not_reused(ctx: VerificationContext) -> Rejection | None:
    # The nonce makes each mandate's digest unique. If some *other* mandate
    # already claimed it, uniqueness has broken and one-time consumption can no
    # longer be relied on.
    claimed_by = ctx.request.mandate_state.nonce_claimed_by
    if claimed_by is not None and claimed_by != ctx.mandate.mandate_id:
        return Rejection(
            ReasonCode.NONCE_REPLAYED,
            f"nonce already belongs to mandate {claimed_by}",
        )
    return None


# --------------------------------------------------------------------------
# Deterministic: is the money within what was approved?
# --------------------------------------------------------------------------


def check_currency_matches(ctx: VerificationContext) -> Rejection | None:
    charged = ctx.charge.amount.currency
    approved = ctx.mandate.max_amount.currency
    if charged is not approved:
        return Rejection(
            ReasonCode.CURRENCY_MISMATCH,
            f"charge is in {charged} but the mandate approved {approved}",
        )
    return None


def check_currency_is_settleable(ctx: VerificationContext) -> Rejection | None:
    approved = ctx.mandate.max_amount.currency
    if approved is not ctx.config.settlement_currency:
        return Rejection(
            ReasonCode.CURRENCY_NOT_SETTLEABLE,
            f"{approved} cannot be settled; this rail settles in "
            f"{ctx.config.settlement_currency}",
        )
    return None


def check_within_cap(ctx: VerificationContext) -> Rejection | None:
    # Safe to compare only because the currency checks above already ran and
    # short-circuited on a mismatch.
    if ctx.charge.amount > ctx.mandate.max_amount:
        return Rejection(
            ReasonCode.CHARGE_OVER_CAP,
            f"charge of {ctx.charge.amount} exceeds the approved cap of "
            f"{ctx.mandate.max_amount}",
        )
    return None


# --------------------------------------------------------------------------
# Deterministic: is the counterparty who they claim to be?
# --------------------------------------------------------------------------


def check_merchant_registered(ctx: VerificationContext) -> Rejection | None:
    if ctx.merchant is None:
        return Rejection(
            ReasonCode.MERCHANT_NOT_REGISTERED,
            f"no registry record for merchant {ctx.charge.merchant_id}",
        )
    return None


def check_merchant_active(ctx: VerificationContext) -> Rejection | None:
    if ctx.merchant is None:  # already rejected by check_merchant_registered
        return None
    if not ctx.merchant.is_active:
        return Rejection(
            ReasonCode.MERCHANT_INACTIVE,
            f"merchant {ctx.merchant.merchant_id} is registered but suspended",
        )
    return None


def check_payout_address(ctx: VerificationContext) -> Rejection | None:
    """The check that closes the sub-seller hole.

    The registry knows the platform, not the sellers on it, so a scam seller
    inside a legitimate marketplace can still list an iPhone for S$5. What they
    cannot do is get paid: funds only ever go to the platform's registered
    address, never to an address supplied in a listing payload.
    """
    if ctx.merchant is None:  # already rejected by check_merchant_registered
        return None
    if ctx.charge.payout_address != ctx.merchant.payout_address:
        return Rejection(
            ReasonCode.PAYOUT_ADDRESS_MISMATCH,
            f"charge pays out to {ctx.charge.payout_address}, but "
            f"{ctx.merchant.merchant_id} is registered as "
            f"{ctx.merchant.payout_address}",
        )
    return None


def check_merchant_binding(ctx: VerificationContext) -> Rejection | None:
    """Only applies once a human has approved a specific merchant."""
    bound_to = ctx.mandate.merchant_address
    if bound_to is None or ctx.merchant is None:
        return None
    if bound_to != ctx.merchant.payout_address:
        return Rejection(
            ReasonCode.MERCHANT_BINDING_MISMATCH,
            f"mandate is bound to {bound_to}, not to {ctx.merchant.merchant_id}",
        )
    return None


def check_basket_binding(ctx: VerificationContext) -> Rejection | None:
    """Only applies once a human has approved a specific basket.

    A mandate minted at the start of a purchase has no basket — the buyer
    approved a budget and an intent, not a SKU. After a REVIEW approval it does,
    and from then on the basket cannot change underneath it.
    """
    bound_to = ctx.mandate.basket_hash
    if bound_to is None:
        return None
    if bound_to != ctx.charge.basket_hash:
        return Rejection(
            ReasonCode.BASKET_BINDING_MISMATCH,
            "the basket being charged is not the basket that was approved",
        )
    return None


# --------------------------------------------------------------------------
# Deterministic: is the evidence genuine?
# --------------------------------------------------------------------------


def check_evaluator_registered(ctx: VerificationContext) -> Rejection | None:
    evaluator = ctx.request.evaluator
    named = ctx.evaluation.evaluator_id
    if evaluator is None or evaluator.agent_id != named:
        return Rejection(
            ReasonCode.EVALUATOR_NOT_REGISTERED,
            f"no registered evaluator matching {named}",
        )
    if evaluator.role is not AgentRole.EVALUATOR or not evaluator.is_active:
        return Rejection(
            ReasonCode.EVALUATOR_NOT_REGISTERED,
            f"agent {named} is not an active evaluator",
        )
    return None


def check_evaluator_signature(ctx: VerificationContext) -> Rejection | None:
    """Stops a compromised Browser Agent writing itself a clean risk score."""
    evaluator = ctx.request.evaluator
    if evaluator is None:  # already rejected by check_evaluator_registered
        return None
    signed = ctx.request.evaluation
    recomputed = evaluation_digest(ctx.evaluation)
    if signed.digest != recomputed or not signed_by(
        to_bytes(recomputed), signed.signature, evaluator.address
    ):
        return Rejection(
            ReasonCode.EVALUATOR_SIGNATURE_INVALID,
            f"evaluation is not signed by {evaluator.agent_id}",
        )
    return None


def check_evaluator_subject(ctx: VerificationContext) -> Rejection | None:
    """Stops a clean evaluation being lifted off one charge onto another.

    Without this, signing the evaluator's output would buy nothing: an attacker
    could reuse a genuine low-risk verdict from a S$4 toothbrush to wave through
    a S$4000 gift card.
    """
    subject = ctx.evaluation.subject
    charge = ctx.charge
    mismatched = (
        subject.mandate_id != charge.mandate_id
        or subject.basket_hash != charge.basket_hash
        or subject.amount != charge.amount
    )
    if mismatched:
        return Rejection(
            ReasonCode.EVALUATOR_SUBJECT_MISMATCH,
            "the evaluation was produced for a different charge",
        )
    return None


# --------------------------------------------------------------------------
# Judgement: what does the evidence suggest?
# --------------------------------------------------------------------------


def check_risk_score(ctx: VerificationContext) -> Rejection | None:
    score = ctx.evaluation.risk_score
    if score > ctx.config.review_score_max:
        return Rejection(
            ReasonCode.RISK_SCORE_CRITICAL,
            f"risk score {score} is above the review band",
        )
    if score > ctx.config.pass_score_max:
        return Rejection(
            ReasonCode.RISK_SCORE_REVIEW_BAND,
            f"risk score {score} needs a human decision",
            Decision.REVIEW,
        )
    return None


def check_intent_match(ctx: VerificationContext) -> Rejection | None:
    """The gap left by minting the mandate before the product was chosen."""
    if not ctx.evaluation.flags.intent_match:
        return Rejection(
            ReasonCode.INTENT_MISMATCH_SUSPECTED,
            "the candidate product may not be what the buyer asked for",
            Decision.REVIEW,
        )
    return None


def check_injection(ctx: VerificationContext) -> Rejection | None:
    if ctx.evaluation.flags.injection_suspected:
        return Rejection(
            ReasonCode.INJECTION_SUSPECTED,
            "the listing text contains instructions aimed at the agent",
            Decision.REVIEW,
        )
    return None


def check_seller_pricing(ctx: VerificationContext) -> Rejection | None:
    """A brand new seller *and* a far-below-market price. Either alone is
    ordinary; together they are the classic marketplace scam."""
    flags = ctx.evaluation.flags
    if flags.price_far_below_market and flags.seller_is_new:
        return Rejection(
            ReasonCode.SUSPICIOUS_SELLER_PRICING,
            "an unknown seller is pricing far below market",
            Decision.REVIEW,
        )
    return None


#: Facts. Run first, short-circuit on the first failure, always FAIL.
DETERMINISTIC_CHECKS = (
    check_mandate_digest,
    check_issuer_signature,
    check_charge_targets_mandate,
    check_not_expired,
    check_not_revoked,
    check_not_already_consumed,
    check_kill_switch,
    check_nonce_not_reused,
    check_currency_matches,
    check_currency_is_settleable,
    check_within_cap,
    check_merchant_registered,
    check_merchant_active,
    check_payout_address,
    check_merchant_binding,
    check_basket_binding,
    check_evaluator_registered,
    check_evaluator_signature,
    check_evaluator_subject,
)

#: Judgement calls. Run only once every fact passes; collect every reason.
JUDGEMENT_CHECKS = (
    check_risk_score,
    check_intent_match,
    check_injection,
    check_seller_pricing,
)
