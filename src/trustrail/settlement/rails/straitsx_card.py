"""The fallback rail: a one-time StraitsX card, paid for in XSGD on Avalanche.

Not a fiat card rail. The card is *bought* onchain: the card API answers with an
HTTP 402, we sign an EIP-3009 `TransferWithAuthorization` for XSGD, and it
settles the transfer and returns the card. So this rail moves XSGD on C-Chain
just as `X402OnchainRail` does — what differs is the enforcement in between.

**Read this before deploying it.** `MandateRegistry.spend` re-checks the cap,
the merchant, the expiry and one-time consumption in Solidity; nothing offchain
can move money outside a mandate. This rail has no contract in the middle. An
EIP-3009 authorisation is a signed instruction to move tokens, and the key that
signs it can authorise any transfer up to the wallet's balance. On this rail the
mandate is enforced by the Verifier and by nothing else.

That is the coverage gradient CLAUDE.md describes, and the mitigation is
operational: point `signer` at a wallet funded to one mandate's cap, so a
compromise costs what is in that wallet rather than the buyer's balance. The
rail refuses to sign for more than the approved charge, and re-checks the 402
against it, but those are our checks — not the chain's.

This rail also cannot bind a basket hash. Degraded enforcement, not absent:
cap, merchant and expiry still hold.

No MCP client is needed. The MCP server is an agent-facing wrapper that hands
out this URL and these steps; the payment itself is plain HTTPS.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from trustrail.clock import SystemClock
from trustrail.models.audit import SettlementOutcome
from trustrail.models.money import Currency
from trustrail.ports import Clock, Signer
from trustrail.settlement.models import SettlementInstruction, SettlementReceipt
from trustrail.signing.eip3009 import TransferAuthorization, sign_authorization
from trustrail.x402.public_spec import (
    PAYMENT_REQUIRED_STATUS,
    REQUIREMENTS_HEADER,
    SIGNATURE_HEADER,
    PaymentRequirements,
    QuotedTooMuch,
    UnsupportedRequirements,
    build_signature_header,
    decode_requirements_header,
    parse_requirements,
)

logger = logging.getLogger(__name__)

RAIL_NAME = "straitsx-card"

SANDBOX_ISSUE_URL = "https://card.straitsx.ai/sandbox/cardapi/issue_card"
PRODUCTION_ISSUE_URL = "https://card.straitsx.ai/production/cardapi/issue_card"

#: Minor units in one SGD. The card API prices in whole dollars and quotes in
#: minor units; 5 SGD came back as 5000000, which independently confirms the
#: 6 decimals `CURRENCY_DECIMALS` assumes.
MINOR_UNITS_PER_SGD = 1_000_000

#: The card API's own limits, in whole SGD. Rejecting locally turns a remote
#: 4xx into a reason the dashboard can render -- and the demo's S$4.20
#: toothbrush is *below* the minimum, which is exactly the sort of thing worth
#: discovering here rather than live.
MIN_CARD_SGD = 5
MAX_CARD_SGD = 30


class StraitsXCardRail:
    """Buys a one-time card for the approved amount, paying over x402."""

    def __init__(
        self,
        *,
        signer: Signer,
        cardholder_name: str,
        issue_url: str = SANDBOX_ISSUE_URL,
        client: httpx.Client | None = None,
        clock: Clock | None = None,
        timeout: float = 30.0,
    ) -> None:
        """`signer` holds the mandate-scoped wallet's key. See the module docstring.

        `issue_url` defaults to sandbox on purpose. Production issues cards that
        spend real money and requires whitelisting, so reaching it should be a
        deliberate act of configuration rather than the default.
        """
        self._signer = signer
        self._cardholder_name = cardholder_name
        self._issue_url = issue_url
        self._client = client
        self._clock = clock or SystemClock()
        self._timeout = timeout

    @property
    def name(self) -> str:
        return RAIL_NAME

    @property
    def wallet_address(self) -> str:
        """The wallet that pays. Fund this to one mandate's cap, and no more."""
        return self._signer.address

    def settle(self, instruction: SettlementInstruction) -> SettlementReceipt:
        try:
            return self._settle(instruction)
        except (UnsupportedRequirements, QuotedTooMuch, ValueError) as error:
            # The rail worked and the deal was not acceptable. Retrying cannot
            # change a merchant's terms, so this is REFUSED rather than ERROR.
            logger.warning(
                "card issuance refused for %s: %s", instruction.mandate_id, error
            )
            return self._receipt(
                instruction, SettlementOutcome.REFUSED, detail=str(error)
            )
        except httpx.HTTPError as error:
            # Transport. Redelivery may well succeed.
            logger.exception("card API transport failed for %s", instruction.mandate_id)
            return self._receipt(
                instruction,
                SettlementOutcome.ERROR,
                detail=f"{type(error).__name__}: {error}",
            )

    def _settle(self, instruction: SettlementInstruction) -> SettlementReceipt:
        amount = instruction.amount
        if amount.currency is not Currency.XSGD:
            raise ValueError(f"this rail settles XSGD, not {amount.currency}")

        card_sgd = self._card_value(instruction)
        body = {"amount_sgd": card_sgd, "cardholder_name": self._cardholder_name}

        with self._session() as client:
            challenge = client.post(self._issue_url, json=body, timeout=self._timeout)
            if challenge.status_code != PAYMENT_REQUIRED_STATUS:
                raise ValueError(
                    f"expected {PAYMENT_REQUIRED_STATUS} from the card API, got "
                    f"{challenge.status_code}"
                )

            requirements = self._requirements(challenge)
            requirements.assert_supported()
            # The merchant quoted a price. Refuse it if it exceeds what the
            # Verifier approved, before signing anything: an authorisation is a
            # bearer instrument once it exists.
            requirements.assert_affordable(amount)

            header = self._authorise(requirements, self._clock.now())
            issued = client.post(
                self._issue_url,
                json=body,
                headers={SIGNATURE_HEADER: header},
                timeout=self._timeout,
            )

        if issued.status_code >= 400:
            raise ValueError(
                f"card API refused the payment: {issued.status_code} "
                f"{issued.text[:200]}"
            )

        return self._issued_receipt(instruction, issued.json())

    def _card_value(self, instruction: SettlementInstruction) -> int:
        """The card's face value in whole SGD, refusing what the API cannot issue.

        Cards come in whole dollars, so a charge with cents cannot be expressed
        exactly. Rounding *up* would spend more than the buyer approved, so this
        refuses instead and says so.
        """
        whole, remainder = divmod(instruction.amount.minor_units, MINOR_UNITS_PER_SGD)
        if remainder:
            raise ValueError(
                f"card values are whole SGD; {instruction.amount} cannot be issued "
                f"exactly, and rounding up would exceed the approved amount"
            )
        if not MIN_CARD_SGD <= whole <= MAX_CARD_SGD:
            raise ValueError(
                f"card values are S${MIN_CARD_SGD}-{MAX_CARD_SGD}; "
                f"{instruction.amount} is outside that range"
            )
        return whole

    @staticmethod
    def _requirements(response: httpx.Response) -> PaymentRequirements:
        """Read the challenge, preferring the header the spec defines."""
        header = response.headers.get(REQUIREMENTS_HEADER)
        required = (
            decode_requirements_header(header)
            if header
            else parse_requirements(response.content)
        )
        return required.first_supported()

    def _authorise(self, requirements: PaymentRequirements, now: datetime) -> str:
        authorization = TransferAuthorization.create(
            from_address=self._signer.address,
            to=requirements.pay_to,
            value=requirements.minor_units,
            now=now,
        )
        signature = sign_authorization(
            authorization, requirements.token_domain(), self._signer
        )
        return build_signature_header(
            requirements=requirements,
            authorization=authorization,
            signature=signature,
        )

    def _issued_receipt(
        self, instruction: SettlementInstruction, payload: dict
    ) -> SettlementReceipt:
        """Turn the card API's success body into a receipt.

        `settlement_tx` is the reference rather than the card id, because the
        transaction is the part anyone else can verify. The card id is ours.
        """
        settlement_tx = payload.get("settlement_tx")
        card_id = payload.get("card_opaque_id")
        if not settlement_tx:
            raise ValueError("card API reported success without a settlement_tx")
        logger.info(
            "card issued",
            extra={"mandate_id": instruction.mandate_id, "card_opaque_id": card_id},
        )
        return self._receipt(
            instruction,
            SettlementOutcome.SETTLED,
            reference=settlement_tx,
            detail=f"card {card_id}" if card_id else None,
        )

    def _session(self):
        """An httpx client, borrowed or built.

        Borrowed in tests so a transport can be injected; built per settlement
        otherwise, because the worker settles rarely and a pooled connection
        held open between messages buys nothing.
        """
        if self._client is not None:
            return _Borrowed(self._client)
        return httpx.Client(timeout=self._timeout)

    def _receipt(
        self,
        instruction: SettlementInstruction,
        status: SettlementOutcome,
        *,
        reference: str | None = None,
        detail: str | None = None,
    ) -> SettlementReceipt:
        return SettlementReceipt(
            mandate_id=instruction.mandate_id,
            rail=self.name,
            status=status,
            reference=reference,
            detail=detail,
        )


class _Borrowed:
    """Lends a client to a `with` block without closing it on the way out."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def __enter__(self) -> httpx.Client:
        return self._client

    def __exit__(self, *_: object) -> None:
        return None
