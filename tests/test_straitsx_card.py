"""The StraitsX card rail, and the EIP-3009 signing underneath it.

The 402 challenge below is the real one, captured from
`https://card.straitsx.ai/sandbox/cardapi/issue_card` for a S$5 card. Using the
live shape rather than an invented one is the point: this rail's whole job is to
speak somebody else's protocol correctly, and a fixture we made up would only
prove we are consistent with ourselves.

Nothing here touches the network. The transport is mocked, so the tests assert
what we *send* — which is the part we control and the part that must be right
before an authorisation is ever signed for real.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from trustrail.models.audit import SettlementOutcome
from trustrail.models.money import Currency, Money
from trustrail.models.primitives import to_bytes
from trustrail.settlement.models import SettlementInstruction
from trustrail.settlement.rails.straitsx_card import (
    SANDBOX_ISSUE_URL,
    StraitsXCardRail,
)
from trustrail.signing.crypto import signed_by
from trustrail.signing.eip712 import Eip712Domain
from trustrail.signing.eip3009 import (
    TransferAuthorization,
    sign_authorization,
)
from trustrail.signing.local import LocalSigner
from trustrail.x402.public_spec import (
    QuotedTooMuch,
    UnsupportedRequirements,
    parse_requirements,
)

NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)

#: Verbatim from the sandbox, for a S$5 card.
LIVE_402 = {
    "x402Version": 1,
    "error": "PAYMENT-SIGNATURE header is required",
    "accepts": [
        {
            "scheme": "exact",
            "network": "eip155:43113",
            "amount": "5000000",
            "asset": "0xd769410dc8772695a7f55a304d2125320a65c2a5",
            "payTo": "0x99a2B2962a6AC463FBe04664027Fdb3F68bd4Cc8",
            "maxTimeoutSeconds": 300,
            "chainId": 43113,
            "extra": {
                "assetTransferMethod": "eip3009",
                "name": "XSGD",
                "version": "2",
            },
        }
    ],
}
ISSUED = {
    "card_opaque_id": "card_abc123",
    "card_html": "<iframe src='...'></iframe>",
    "settlement_tx": "0x" + "ab" * 32,
}


class FrozenClock:
    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at


@pytest.fixture
def wallet() -> LocalSigner:
    """The mandate-scoped hot wallet that pays for cards."""
    return LocalSigner.generate()


def instruction(amount: str = "5.00") -> SettlementInstruction:
    return SettlementInstruction(
        mandate_id="0x" + "11" * 32,
        charge_id="0x" + "22" * 32,
        principal="0x" + "33" * 20,
        merchant_id="mrc_stub_sg",
        payout_address="0x" + "44" * 20,
        amount=Money(currency=Currency.XSGD, amount=amount),
        basket_hash="0x" + "55" * 32,
        quote_id="q_01HXTEST",
        sku="TB-SOFT-2PK",
        quantity=1,
    )


def rail_over(handler, wallet: LocalSigner) -> StraitsXCardRail:
    return StraitsXCardRail(
        signer=wallet,
        cardholder_name="Trust Rail Demo",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=FrozenClock(NOW),
    )


def paying_handler(sent: list[httpx.Request], challenge: dict | None = None):
    """A card API that 402s once, then accepts whatever signature comes back."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if "PAYMENT-SIGNATURE" not in request.headers:
            return httpx.Response(402, json=challenge or LIVE_402)
        return httpx.Response(200, json=ISSUED)

    return handler


class TestEip3009Signing:
    def test_the_digest_is_stable(self):
        """A golden value. If this moves, every signature we ever made is invalid."""
        authorization = TransferAuthorization(
            from_address="0x" + "aa" * 20,
            to="0x" + "bb" * 20,
            value=5_000_000,
            valid_after=0,
            valid_before=1_800_000_000,
            nonce="0x" + "cc" * 32,
        )
        domain = Eip712Domain(
            name="XSGD",
            version="2",
            chain_id=43113,
            verifying_contract="0xd769410dc8772695a7f55a304d2125320a65c2a5",
        )

        digest = authorization.digest(domain)

        assert digest == (
            "0xbd6361a5573340eabdd5c0d75108d9f4f5fc3804bc3963327fb5848407572a4e"
        )

    def test_our_encoding_agrees_with_an_independent_implementation(self):
        """The check that actually matters.

        A golden digest only proves we are consistent with ourselves, and a
        self-consistent but wrong EIP-712 encoding produces signatures the
        token contract rejects — discovered against somebody else's API, on
        demo day. `eth_account` implements the spec separately, so agreeing
        with it is evidence about the spec rather than about our own arithmetic.
        """
        eth_account = pytest.importorskip("eth_account.messages")
        keccak = pytest.importorskip("eth_utils").keccak

        message = eth_account.encode_typed_data(
            full_message={
                "types": {
                    "EIP712Domain": [
                        {"name": "name", "type": "string"},
                        {"name": "version", "type": "string"},
                        {"name": "chainId", "type": "uint256"},
                        {"name": "verifyingContract", "type": "address"},
                    ],
                    "TransferWithAuthorization": [
                        {"name": "from", "type": "address"},
                        {"name": "to", "type": "address"},
                        {"name": "value", "type": "uint256"},
                        {"name": "validAfter", "type": "uint256"},
                        {"name": "validBefore", "type": "uint256"},
                        {"name": "nonce", "type": "bytes32"},
                    ],
                },
                "primaryType": "TransferWithAuthorization",
                "domain": {
                    "name": "XSGD",
                    "version": "2",
                    "chainId": 43113,
                    "verifyingContract": (
                        "0xd769410dc8772695a7f55a304d2125320a65c2a5"
                    ),
                },
                "message": {
                    "from": "0x" + "aa" * 20,
                    "to": "0x" + "bb" * 20,
                    "value": 5_000_000,
                    "validAfter": 0,
                    "validBefore": 1_800_000_000,
                    "nonce": bytes.fromhex("cc" * 32),
                },
            }
        )
        reference = (
            "0x"
            + keccak(b"\x19" + message.version + message.header + message.body).hex()
        )

        authorization = TransferAuthorization(
            from_address="0x" + "aa" * 20,
            to="0x" + "bb" * 20,
            value=5_000_000,
            valid_after=0,
            valid_before=1_800_000_000,
            nonce="0x" + "cc" * 32,
        )
        domain = Eip712Domain(
            name="XSGD",
            version="2",
            chain_id=43113,
            verifying_contract="0xd769410dc8772695a7f55a304d2125320a65c2a5",
        )

        assert authorization.digest(domain) == reference

    def test_a_signature_recovers_to_the_signing_wallet(self, wallet):
        domain = parse_requirements(LIVE_402).first_supported().token_domain()
        authorization = TransferAuthorization.create(
            from_address=wallet.address, to="0x" + "bb" * 20, value=5_000_000, now=NOW
        )

        signature = sign_authorization(authorization, domain, wallet)

        assert signed_by(
            to_bytes(authorization.digest(domain)), signature, wallet.address
        )

    def test_a_wallet_cannot_authorise_someone_elses_tokens(self, wallet):
        domain = parse_requirements(LIVE_402).first_supported().token_domain()
        authorization = TransferAuthorization.create(
            from_address="0x" + "ee" * 20, to="0x" + "bb" * 20, value=1, now=NOW
        )

        with pytest.raises(ValueError, match="can only authorise its own"):
            sign_authorization(authorization, domain, wallet)

    def test_the_window_is_bounded_but_starts_open(self):
        """valid_after is 0 so a node with a slow clock cannot reject a good one."""
        authorization = TransferAuthorization.create(
            from_address="0x" + "aa" * 20, to="0x" + "bb" * 20, value=1, now=NOW
        )

        assert authorization.valid_after == 0
        assert authorization.valid_before == int((NOW + timedelta(minutes=5)).timestamp())


class TestReadingTheChallenge:
    def test_the_live_challenge_parses(self):
        requirements = parse_requirements(LIVE_402).first_supported()

        assert requirements.minor_units == 5_000_000
        assert requirements.pay_to == "0x99a2b2962a6ac463fbe04664027fdb3f68bd4cc8"
        assert requirements.asset == "0xd769410dc8772695a7f55a304d2125320a65c2a5"

    def test_the_token_domain_comes_from_the_merchant_not_from_us(self):
        """Signing under our own TrustRail domain would produce a rejected signature."""
        domain = parse_requirements(LIVE_402).first_supported().token_domain()

        assert domain.name == "XSGD"
        assert domain.version == "2"
        assert domain.chain_id == 43113
        assert domain.verifying_contract == "0xd769410dc8772695a7f55a304d2125320a65c2a5"

    def test_an_unsupported_transfer_method_is_refused(self):
        challenge = json.loads(json.dumps(LIVE_402))
        challenge["accepts"][0]["extra"]["assetTransferMethod"] = "permit2"

        with pytest.raises(UnsupportedRequirements):
            parse_requirements(challenge).first_supported()

    def test_a_merchant_asking_more_than_approved_is_refused(self):
        requirements = parse_requirements(LIVE_402).first_supported()

        with pytest.raises(QuotedTooMuch):
            requirements.assert_affordable(Money(currency=Currency.XSGD, amount="4.00"))


class TestSettling:
    def test_it_pays_the_402_and_returns_the_settlement_transaction(self, wallet):
        sent: list[httpx.Request] = []
        rail = rail_over(paying_handler(sent), wallet)

        receipt = rail.settle(instruction("5.00"))

        assert receipt.status is SettlementOutcome.SETTLED
        assert receipt.reference == ISSUED["settlement_tx"]
        assert "card_abc123" in receipt.detail
        assert len(sent) == 2  # the challenge, then the payment

    def test_what_we_sign_is_what_the_merchant_asked_for(self, wallet):
        sent: list[httpx.Request] = []
        rail = rail_over(paying_handler(sent), wallet)

        rail.settle(instruction("5.00"))

        payload = json.loads(base64.b64decode(sent[1].headers["PAYMENT-SIGNATURE"]))
        authorization = payload["payload"]["authorization"]

        assert payload["scheme"] == "exact"
        assert payload["network"] == "eip155:43113"
        assert authorization["from"] == wallet.address
        assert authorization["to"] == "0x99a2b2962a6ac463fbe04664027fdb3f68bd4cc8"
        assert authorization["value"] == "5000000"

    def test_the_signature_we_send_verifies_against_our_wallet(self, wallet):
        sent: list[httpx.Request] = []
        rail = rail_over(paying_handler(sent), wallet)

        rail.settle(instruction("5.00"))

        payload = json.loads(base64.b64decode(sent[1].headers["PAYMENT-SIGNATURE"]))
        fields = payload["payload"]["authorization"]
        rebuilt = TransferAuthorization(
            from_address=fields["from"],
            to=fields["to"],
            value=int(fields["value"]),
            valid_after=int(fields["validAfter"]),
            valid_before=int(fields["validBefore"]),
            nonce=fields["nonce"],
        )
        domain = parse_requirements(LIVE_402).first_supported().token_domain()

        assert signed_by(
            to_bytes(rebuilt.digest(domain)),
            payload["payload"]["signature"],
            wallet.address,
        )

    def test_amounts_are_never_rounded_up_to_reach_the_minimum(self, wallet):
        """The demo toothbrush is S$4.20 and this rail cannot issue it.

        Rounding up to S$5 would spend eighty cents the buyer never approved,
        so the rail refuses and says why.
        """
        sent: list[httpx.Request] = []
        rail = rail_over(paying_handler(sent), wallet)

        receipt = rail.settle(instruction("4.20"))

        assert receipt.status is SettlementOutcome.REFUSED
        assert "whole SGD" in receipt.detail
        assert sent == []  # refused before the card API was ever called

    def test_a_value_outside_the_card_range_is_refused(self, wallet):
        rail = rail_over(paying_handler([]), wallet)

        receipt = rail.settle(instruction("31.00"))

        assert receipt.status is SettlementOutcome.REFUSED
        assert "S$5-30" in receipt.detail

    def test_a_merchant_that_raises_its_price_is_refused_before_signing(self, wallet):
        """The 402 asks 5.00 but the Verifier only approved 3.00."""
        sent: list[httpx.Request] = []
        greedy = json.loads(json.dumps(LIVE_402))
        greedy["accepts"][0]["amount"] = "9000000"
        rail = rail_over(paying_handler(sent, greedy), wallet)

        receipt = rail.settle(instruction("5.00"))

        assert receipt.status is SettlementOutcome.REFUSED
        assert "more than the approved" in receipt.detail
        assert "PAYMENT-SIGNATURE" not in sent[-1].headers

    def test_a_transport_fault_is_retryable(self, wallet):
        def broken(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("card API unreachable")

        receipt = rail_over(broken, wallet).settle(instruction("5.00"))

        assert receipt.status is SettlementOutcome.ERROR
        assert receipt.retryable

    def test_a_refusal_is_not_retryable(self, wallet):
        receipt = rail_over(paying_handler([]), wallet).settle(instruction("4.20"))

        assert not receipt.retryable

    def test_success_without_a_settlement_tx_is_not_treated_as_settled(self, wallet):
        """No transaction, no proof. We do not take the merchant's word for it."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "PAYMENT-SIGNATURE" not in request.headers:
                return httpx.Response(402, json=LIVE_402)
            return httpx.Response(200, json={"card_opaque_id": "card_x"})

        receipt = rail_over(handler, wallet).settle(instruction("5.00"))

        assert receipt.status is SettlementOutcome.REFUSED
        assert "settlement_tx" in receipt.detail


def test_the_rail_defaults_to_sandbox(wallet):
    """Production issues cards that spend real money; reaching it must be deliberate."""
    rail = StraitsXCardRail(signer=wallet, cardholder_name="Trust Rail Demo")

    assert rail._issue_url == SANDBOX_ISSUE_URL
    assert rail.wallet_address == wallet.address
