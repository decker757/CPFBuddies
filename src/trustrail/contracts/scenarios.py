"""The golden scenarios: one per thing we claim to stop.

Each scenario is a complete `VerificationRequest` plus the verdict it must
produce. They serve three jobs at once, which is why they live in the package
rather than in the test directory:

- they are the fixtures workstreams B, C and D code against;
- they are the Verifier's test corpus, so behaviour and fixtures cannot drift;
- they are the demo script, in order.

Scenarios are built by mutating one known-good request. That keeps each one to a
few readable lines and makes the difference between "this settles" and "this
does not" obvious at a glance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from trustrail.contracts.keys import (
    BROWSER_AGENT_ID,
    EVALUATOR_ADDRESS,
    EVALUATOR_ID,
    EVALUATOR_PRIVATE_KEY,
    IMPOSTOR_PRIVATE_KEY,
    ISSUER_ADDRESS,
    ISSUER_PRIVATE_KEY,
    MERCHANT_ID,
    label_to_address,
    label_to_hash,
)
from trustrail.models.charge import Charge
from trustrail.models.evaluation import (
    EvaluationSubject,
    EvaluatorFlags,
    EvaluatorOutput,
    SignedEvaluatorOutput,
)
from trustrail.models.mandate import (
    Mandate,
    MandateState,
    MandateStatus,
    SignedMandate,
)
from trustrail.models.money import Currency, Money
from trustrail.models.primitives import to_bytes
from trustrail.models.registry import AgentRecord, AgentRole, MerchantRecord
from trustrail.models.review import ReviewHold
from trustrail.models.verdict import Decision, ReasonCode, Verdict
from trustrail.models.verification import VerificationRequest
from trustrail.signing.crypto import sign_digest
from trustrail.signing.eip712 import Eip712Domain, mandate_digest
from trustrail.signing.evidence import evaluation_digest
from trustrail.verifier.config import VerifierConfig

#: A fixed instant, so every fixture is byte-reproducible.
DEMO_NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
DEMO_WINDOW = timedelta(minutes=10)

PRINCIPAL = label_to_address("principal:ernest")
MERCHANT_PAYOUT = label_to_address("merchant:sgmart")
ATTACKER_PAYOUT = label_to_address("attacker:wallet")
BASKET_HASH = label_to_hash("basket:toothbrush-2pk")
OTHER_BASKET_HASH = label_to_hash("basket:gift-card")

INTENT = "toothbrush under $5"
CAP = Money(currency=Currency.XSGD, amount="5.00")
PRICE = Money(currency=Currency.XSGD, amount="4.20")


@dataclass(frozen=True, slots=True)
class Scenario:
    """One request and the verdict it must produce."""

    name: str
    description: str
    request: VerificationRequest
    expected_decision: Decision
    expected_reasons: tuple[ReasonCode, ...] = ()


def demo_config() -> VerifierConfig:
    """The Verifier settings the fixtures were generated under."""
    return VerifierConfig(issuer_address=ISSUER_ADDRESS, domain=Eip712Domain())


class ScenarioBuilder:
    """Assembles valid requests, so scenarios only have to state their deviation."""

    def __init__(self, domain: Eip712Domain | None = None) -> None:
        self.domain = domain or Eip712Domain()

    # --- building blocks -------------------------------------------------

    def mandate(self, **overrides: Any) -> Mandate:
        fields: dict[str, Any] = {
            "mandate_id": label_to_hash("mandate:demo"),
            "principal": PRINCIPAL,
            "agent_id": BROWSER_AGENT_ID,
            "max_amount": CAP,
            "expires_at": DEMO_NOW + DEMO_WINDOW,
            "intent": INTENT,
            "nonce": label_to_hash("nonce:demo"),
        }
        return Mandate(**(fields | overrides))

    def sign_mandate(self, mandate: Mandate) -> SignedMandate:
        digest = mandate_digest(mandate, self.domain)
        return SignedMandate(
            mandate=mandate,
            digest=digest,
            signature=sign_digest(ISSUER_PRIVATE_KEY, to_bytes(digest)),
        )

    def charge(self, **overrides: Any) -> Charge:
        fields: dict[str, Any] = {
            "charge_id": label_to_hash("charge:demo"),
            "mandate_id": label_to_hash("mandate:demo"),
            "merchant_id": MERCHANT_ID,
            "payout_address": MERCHANT_PAYOUT,
            "amount": PRICE,
            "basket_hash": BASKET_HASH,
            "quote_id": "q_01HX7TDEMO",
            "sku": "TB-SOFT-2PK",
            "title": "Soft bristle toothbrush, 2 pack",
            "quantity": 1,
        }
        return Charge(**(fields | overrides))

    def evaluation(self, charge: Charge, **overrides: Any) -> EvaluatorOutput:
        fields: dict[str, Any] = {
            "evaluator_id": EVALUATOR_ID,
            "subject": EvaluationSubject(
                mandate_id=charge.mandate_id,
                basket_hash=charge.basket_hash,
                amount=charge.amount,
            ),
            "risk_score": 2,
            "flags": EvaluatorFlags(
                intent_match=True,
                injection_suspected=False,
                price_far_below_market=False,
                seller_is_new=False,
            ),
            "reasons": ["Product matches the stated intent.", "Price is within cap."],
        }
        return EvaluatorOutput(**(fields | overrides))

    def sign_evaluation(
        self, evaluation: EvaluatorOutput, *, key: bytes = EVALUATOR_PRIVATE_KEY
    ) -> SignedEvaluatorOutput:
        digest = evaluation_digest(evaluation)
        return SignedEvaluatorOutput(
            evaluation=evaluation,
            digest=digest,
            signature=sign_digest(key, to_bytes(digest)),
        )

    def forge_mandate(self, mandate: Mandate, *, stolen: str) -> SignedMandate:
        """An honestly hashed mandate carrying somebody else's signature.

        This is what a tampering attacker can actually do: the digest is public
        so they can recompute it, but the issuer key is in KMS so the best they
        can supply is a signature lifted from a mandate they were given.
        """
        return SignedMandate(
            mandate=mandate,
            digest=mandate_digest(mandate, self.domain),
            signature=stolen,
        )

    def merchant(self, **overrides: Any) -> MerchantRecord:
        fields: dict[str, Any] = {
            "merchant_id": MERCHANT_ID,
            "name": "SG Mart",
            "payout_address": MERCHANT_PAYOUT,
            "is_active": True,
        }
        return MerchantRecord(**(fields | overrides))

    def evaluator_record(self) -> AgentRecord:
        return AgentRecord(
            agent_id=EVALUATOR_ID,
            role=AgentRole.EVALUATOR,
            address=EVALUATOR_ADDRESS,
        )

    def hold(self, verdict: Verdict, **overrides: Any) -> ReviewHold:
        """A charge paused for a human, consistent with the verdict that paused it.

        The charge and the evidence are derived from the same verdict rather
        than passed separately, because a hold whose parts disagree is exactly
        what `ReviewHold` refuses to be built from.
        """
        charge = overrides.pop("charge", None) or self.charge(
            charge_id=verdict.charge_id, mandate_id=verdict.mandate_id
        )
        fields: dict[str, Any] = {
            "charge_id": charge.charge_id,
            "mandate_id": charge.mandate_id,
            "verdict": verdict,
            "charge": charge,
            "evaluation": self.sign_evaluation(self.evaluation(charge)),
            "held_at": DEMO_NOW,
            "deadline": DEMO_NOW + DEMO_WINDOW,
        }
        return ReviewHold(**(fields | overrides))

    # --- the known-good request ------------------------------------------

    def request(self, **overrides: Any) -> VerificationRequest:
        """A request that passes every check, unless an override breaks it."""
        charge = overrides.pop("charge", None) or self.charge()
        signed_mandate = overrides.pop("signed_mandate", None) or self.sign_mandate(
            self.mandate()
        )
        evaluation = overrides.pop("evaluation", None) or self.sign_evaluation(
            self.evaluation(charge)
        )
        fields: dict[str, Any] = {
            "signed_mandate": signed_mandate,
            "charge": charge,
            "evaluation": evaluation,
            "mandate_state": MandateState(status=MandateStatus.MINTED),
            "merchant": self.merchant(),
            "evaluator": self.evaluator_record(),
            "kill_switch_active": False,
            "now": DEMO_NOW,
        }
        return VerificationRequest(**(fields | overrides))


def build_scenarios() -> list[Scenario]:
    """Every scenario, in demo order: the happy path, then what we stop."""
    build = ScenarioBuilder()
    return [
        *_passing_scenarios(build),
        *_human_in_the_loop_scenarios(build),
        *_forgery_scenarios(build),
        *_money_and_lifecycle_scenarios(build),
        *_counterparty_scenarios(build),
    ]


def _passing_scenarios(build: ScenarioBuilder) -> list[Scenario]:
    at_cap = build.charge(amount=CAP)
    approved_mandate = build.mandate(
        merchant_address=MERCHANT_PAYOUT, basket_hash=BASKET_HASH
    )
    return [
        Scenario(
            name="clean_pass",
            description=(
                "The happy path. A toothbrush at S$4.20 against a S$5.00 mandate, "
                "from a registered merchant, with a low risk score."
            ),
            request=build.request(),
            expected_decision=Decision.PASS,
        ),
        Scenario(
            name="cap_boundary_exact",
            description=(
                "A charge for exactly the cap settles. The cap is a limit, not a "
                "budget to stay under, and the comparison is exact — no rounding "
                "slack in either direction."
            ),
            request=build.request(
                charge=at_cap,
                evaluation=build.sign_evaluation(build.evaluation(at_cap)),
            ),
            expected_decision=Decision.PASS,
        ),
        Scenario(
            name="approved_after_review",
            description=(
                "A mandate a human approved during REVIEW: merchant and basket are "
                "now bound and the mandate re-signed. It re-enters the Verifier and "
                "passes every check again — approval supplies more to verify, it "
                "does not skip verification."
            ),
            request=build.request(
                signed_mandate=build.sign_mandate(approved_mandate),
                mandate_state=MandateState(status=MandateStatus.BOUND),
            ),
            expected_decision=Decision.PASS,
        ),
    ]


def _human_in_the_loop_scenarios(build: ScenarioBuilder) -> list[Scenario]:
    substitution = build.evaluation(
        build.charge(),
        risk_score=5,
        flags=EvaluatorFlags(
            intent_match=False,
            injection_suspected=False,
            price_far_below_market=False,
            seller_is_new=False,
        ),
        reasons=["Candidate is an electric toothbrush charger, not a toothbrush."],
    )
    bargain = build.evaluation(
        build.charge(),
        risk_score=6,
        flags=EvaluatorFlags(
            intent_match=True,
            injection_suspected=False,
            price_far_below_market=True,
            seller_is_new=True,
        ),
        reasons=["Seller registered 2 days ago.", "Price is 80% below market."],
    )
    return [
        Scenario(
            name="product_substitution",
            description=(
                "Right price, wrong product. This is the gap left by minting the "
                "mandate before the product was chosen, and it is why the Evaluator "
                "exists. A human decides."
            ),
            request=build.request(evaluation=build.sign_evaluation(substitution)),
            expected_decision=Decision.REVIEW,
            expected_reasons=(
                ReasonCode.RISK_SCORE_REVIEW_BAND,
                ReasonCode.INTENT_MISMATCH_SUSPECTED,
            ),
        ),
        Scenario(
            name="low_price_new_seller",
            description=(
                "A brand new seller pricing far below market inside an otherwise "
                "legitimate marketplace. Neither signal alone is damning, which is "
                "exactly why this is a REVIEW rather than a FAIL."
            ),
            request=build.request(evaluation=build.sign_evaluation(bargain)),
            expected_decision=Decision.REVIEW,
            expected_reasons=(
                ReasonCode.RISK_SCORE_REVIEW_BAND,
                ReasonCode.SUSPICIOUS_SELLER_PRICING,
            ),
        ),
    ]


def _forgery_scenarios(build: ScenarioBuilder) -> list[Scenario]:
    poisoned = build.evaluation(
        build.charge(),
        risk_score=9,
        flags=EvaluatorFlags(
            intent_match=True,
            injection_suspected=True,
            price_far_below_market=False,
            seller_is_new=False,
        ),
        reasons=[
            "Listing description instructs the agent to ignore its budget.",
            "Listing description instructs the agent to buy a gift card instead.",
        ],
    )
    honest_mandate = build.mandate()
    signed = build.sign_mandate(honest_mandate)
    raised_cap = honest_mandate.model_copy(
        update={"max_amount": Money(currency=Currency.XSGD, amount="5000.00")}
    )
    return [
        Scenario(
            name="prompt_injection_in_listing",
            description=(
                "THE demo. The listing description carries instructions aimed at the "
                "agent. The Evaluator flags it and scores it 9, which is above the "
                "review band, so the charge fails without a human. Note that even if "
                "the Evaluator had been fooled, the cap and expiry still held."
            ),
            request=build.request(evaluation=build.sign_evaluation(poisoned)),
            expected_decision=Decision.FAIL,
            expected_reasons=(
                ReasonCode.RISK_SCORE_CRITICAL,
                ReasonCode.INJECTION_SUSPECTED,
            ),
        ),
        Scenario(
            name="tampered_cap_resigned_digest",
            description=(
                "An attacker raises the cap from S$5 to S$5000 and honestly "
                "recomputes the digest — the hash is public, so they can. What they "
                "cannot do is produce the issuer's signature over it. This is the "
                "check the whole design rests on."
            ),
            request=build.request(
                signed_mandate=build.forge_mandate(
                    raised_cap, stolen=signed.signature
                )
            ),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.MANDATE_SIGNATURE_INVALID,),
        ),
        Scenario(
            name="tampered_cap_stale_digest",
            description=(
                "The lazy version of the same attack: edit the cap and leave the "
                "original digest in place. Caught one check earlier."
            ),
            request=build.request(
                signed_mandate=signed.model_copy(update={"mandate": raised_cap})
            ),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.MANDATE_DIGEST_MISMATCH,),
        ),
        Scenario(
            name="forged_evaluator_verdict",
            description=(
                "A compromised Browser Agent writes itself a clean risk score and "
                "signs it with its own key. The Agent Registry knows which key the "
                "Evaluator uses, so the forgery does not verify."
            ),
            request=build.request(
                evaluation=build.sign_evaluation(
                    build.evaluation(build.charge()), key=IMPOSTOR_PRIVATE_KEY
                )
            ),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.EVALUATOR_SIGNATURE_INVALID,),
        ),
        Scenario(
            name="replayed_evaluation",
            description=(
                "A genuine, correctly signed low-risk evaluation — but it was "
                "produced for a different basket. Without the subject binding, "
                "signing the evaluator's output would buy nothing."
            ),
            request=build.request(
                evaluation=build.sign_evaluation(
                    build.evaluation(
                        build.charge(),
                        subject=EvaluationSubject(
                            mandate_id=label_to_hash("mandate:demo"),
                            basket_hash=OTHER_BASKET_HASH,
                            amount=PRICE,
                        ),
                    )
                )
            ),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.EVALUATOR_SUBJECT_MISMATCH,),
        ),
    ]


def _money_and_lifecycle_scenarios(build: ScenarioBuilder) -> list[Scenario]:
    over_cap = build.charge(amount=Money(currency=Currency.XSGD, amount="7.50"))
    one_unit_over = build.charge(
        amount=Money.from_minor_units(CAP.minor_units + 1, Currency.XSGD)
    )
    return [
        Scenario(
            name="over_cap",
            description=(
                "S$7.50 against a S$5.00 mandate. Arithmetic, not judgement: no "
                "approval button, no override. Spending more needs a new mandate."
            ),
            request=build.request(
                charge=over_cap,
                evaluation=build.sign_evaluation(build.evaluation(over_cap)),
            ),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.CHARGE_OVER_CAP,),
        ),
        Scenario(
            name="one_minor_unit_over_cap",
            description=(
                "The cap plus 0.000001 XSGD. Amounts are compared as integer minor "
                "units, so there is no rounding slack to hide a charge in."
            ),
            request=build.request(
                charge=one_unit_over,
                evaluation=build.sign_evaluation(build.evaluation(one_unit_over)),
            ),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.CHARGE_OVER_CAP,),
        ),
        Scenario(
            name="expired_mandate",
            description=(
                "The same clean charge, arriving after the approval window closed. "
                "A mandate the buyer approved ten minutes ago is not consent now."
            ),
            request=build.request(now=DEMO_NOW + timedelta(minutes=20)),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.MANDATE_EXPIRED,),
        ),
        Scenario(
            name="revoked_mandate",
            description="The buyer pulled the mandate before settlement.",
            request=build.request(
                mandate_state=MandateState(status=MandateStatus.REVOKED)
            ),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.MANDATE_REVOKED,),
        ),
        Scenario(
            name="already_consumed",
            description=(
                "A replay of a charge that already settled. Mandates are one-time; "
                "the second attempt has nothing left to spend."
            ),
            request=build.request(
                mandate_state=MandateState(status=MandateStatus.CONSUMED)
            ),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.MANDATE_ALREADY_CONSUMED,),
        ),
        Scenario(
            name="kill_switch_engaged",
            description="The panic button is down. Nothing settles for this buyer.",
            request=build.request(kill_switch_active=True),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.KILL_SWITCH_ACTIVE,),
        ),
        Scenario(
            name="unsettleable_currency",
            description=(
                "A mandate denominated in fiat SGD rather than XSGD. This rail "
                "settles XSGD on Avalanche C-Chain and nothing else."
            ),
            request=_sgd_request(build),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.CURRENCY_NOT_SETTLEABLE,),
        ),
    ]


def _counterparty_scenarios(build: ScenarioBuilder) -> list[Scenario]:
    redirected = build.charge(payout_address=ATTACKER_PAYOUT)
    changed_basket = build.charge(basket_hash=OTHER_BASKET_HASH)
    return [
        Scenario(
            name="payout_address_redirected",
            description=(
                "A scam seller inside a registered marketplace supplies its own "
                "payout address in the listing. XSGD only ever goes to the address "
                "the platform registered, so the money path is closed regardless of "
                "what the listing claims."
            ),
            request=build.request(
                charge=redirected,
                evaluation=build.sign_evaluation(build.evaluation(redirected)),
            ),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.PAYOUT_ADDRESS_MISMATCH,),
        ),
        Scenario(
            name="unregistered_merchant",
            description=(
                "The counterparty is not in the Merchant Registry at all. We verify "
                "platforms, and an unverified platform is not a counterparty."
            ),
            request=build.request(merchant=None),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.MERCHANT_NOT_REGISTERED,),
        ),
        Scenario(
            name="basket_swapped_after_approval",
            description=(
                "A human approved one basket during REVIEW; a different basket "
                "arrives at settlement. Once bound, the basket cannot change "
                "underneath the approval."
            ),
            request=build.request(
                signed_mandate=build.sign_mandate(
                    build.mandate(
                        merchant_address=MERCHANT_PAYOUT, basket_hash=BASKET_HASH
                    )
                ),
                mandate_state=MandateState(status=MandateStatus.BOUND),
                charge=changed_basket,
                evaluation=build.sign_evaluation(build.evaluation(changed_basket)),
            ),
            expected_decision=Decision.FAIL,
            expected_reasons=(ReasonCode.BASKET_BINDING_MISMATCH,),
        ),
    ]


def _sgd_request(build: ScenarioBuilder) -> VerificationRequest:
    """A whole request denominated in fiat SGD rather than XSGD."""
    price = Money(currency=Currency.SGD, amount="4.20")
    charge = build.charge(amount=price)
    mandate = build.mandate(max_amount=Money(currency=Currency.SGD, amount="5.00"))
    return build.request(
        signed_mandate=build.sign_mandate(mandate),
        charge=charge,
        evaluation=build.sign_evaluation(build.evaluation(charge)),
    )
