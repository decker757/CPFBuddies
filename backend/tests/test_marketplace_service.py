from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from trustrail.settlement.chain.deployment import Deployment

from app.contracts import ListingsResponse, PurchaseRequest
from app.marketplace.repository import InMemoryQuoteRepository
from app.marketplace.service import (
    ExpiredQuote,
    MarketplaceService,
    QuoteAlreadyConsumed,
    QuoteIntegrityFailure,
    SettlementProfile,
)

#: XSGD and the MandateRegistry as actually deployed on Avalanche C-Chain.
MAINNET = Deployment(
    network="avalanche",
    chainId=43114,
    settlementToken="0xb2f85b7ab3c2b6f62df06de6ae7d09c010a5096e",
    settlementTokenSymbol="XSGD",
    settlementTokenDecimals=6,
    mandateRegistry="0xdb4050cf28cfa0cb956bfdbcb64341ee1c592c23",
    registrar="0x" + "11" * 20,
    settler="0x" + "22" * 20,
)


class FixedClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


class SequentialIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"q_test_{self.value}"


def make_service(
    repository: InMemoryQuoteRepository | None = None,
    settlement: SettlementProfile | None = None,
) -> tuple[MarketplaceService, InMemoryQuoteRepository, FixedClock]:
    quotes = repository or InMemoryQuoteRepository()
    clock = FixedClock()
    return (
        MarketplaceService(
            quotes=quotes,
            clock=clock,
            quote_ids=SequentialIds(),
            settlement=settlement or SettlementProfile(),
        ),
        quotes,
        clock,
    )


def purchase_request(quote_id: str, sku: str = "TB-SOFT-2PK") -> PurchaseRequest:
    return PurchaseRequest(
        sku=sku,
        quantity=1,
        quote_id=quote_id,
        mandate_credential={"mandateId": "mandate-1"},
        signed_request="0xsigned",
    )


def test_expired_quote_is_rejected() -> None:
    service, _, clock = make_service()
    quote = service.list_items(q="TB-SOFT-2PK", max_price=Decimal("5"), currency="XSGD")
    clock.current += timedelta(minutes=11)

    with pytest.raises(ExpiredQuote):
        service.purchase(purchase_request(quote.quote_id), payment_proof=None)


def test_old_expired_quotes_are_cleaned_up() -> None:
    service, repository, clock = make_service()
    old_quote = service.list_items(q="TB-SOFT-2PK", max_price=None, currency="XSGD")
    clock.current += timedelta(hours=2)

    service.list_items(q="TB-SOFT-2PK", max_price=None, currency="XSGD")
    assert repository.get(old_quote.quote_id) is None


def test_payment_retry_is_idempotent_but_different_proof_is_rejected() -> None:
    service, _, _ = make_service()
    quote = service.list_items(q="TB-SOFT-2PK", max_price=None, currency="XSGD")
    request = purchase_request(quote.quote_id)

    first = service.purchase(request, payment_proof="proof-1")
    retry = service.purchase(request, payment_proof="proof-1")
    assert retry == first

    with pytest.raises(QuoteAlreadyConsumed):
        service.purchase(request, payment_proof="proof-2")


def test_stored_quote_with_tampered_basket_hash_is_rejected() -> None:
    service, repository, _ = make_service()
    quote = service.list_items(q="TB-SOFT-2PK", max_price=None, currency="XSGD")
    corrupt = quote.model_copy(update={"quote_id": "q_corrupt", "basket_hash": f"0x{'0' * 64}"})
    repository.save(ListingsResponse.model_validate(corrupt))

    with pytest.raises(QuoteIntegrityFailure):
        service.purchase(purchase_request("q_corrupt"), payment_proof=None)


def test_empty_catalog_still_returns_a_bound_quote() -> None:
    quotes = InMemoryQuoteRepository()
    service = MarketplaceService(
        quotes=quotes,
        clock=FixedClock(),
        quote_ids=SequentialIds(),
        catalog=(),
    )
    quote = service.list_items(q="anything", max_price=None, currency="XSGD")
    assert quote.items == []
    assert quote.basket_hash.startswith("0x")


def test_the_default_settlement_profile_describes_nothing_deployed() -> None:
    """A zero asset is not a token, and the default must not look plausible.

    The offline suite needs a marketplace with no chain behind it. What it must
    not have is a default that reads like a real deployment, because that is how
    a 402 ends up quoting a network nobody is settling on.
    """
    assert not SettlementProfile().is_configured


def test_the_profile_follows_the_deployment_record() -> None:
    profile = SettlementProfile.from_deployment(MAINNET)

    assert profile.is_configured
    assert profile.chain_id == 43114
    assert profile.asset == MAINNET.settlement_token
    assert profile.network == "avalanche"


def test_a_402_quotes_the_chain_and_asset_that_will_actually_settle() -> None:
    """What the merchant advertises has to be what the worker spends."""
    service, _, _ = make_service(settlement=SettlementProfile.from_deployment(MAINNET))
    quote = service.list_items(q="TB-SOFT-2PK", max_price=None, currency="XSGD")

    required = service.purchase(purchase_request(quote.quote_id), None)

    assert required.payment_terms.chain_id == 43114
    assert required.payment_terms.asset == MAINNET.settlement_token
    assert required.payment_terms.network == "avalanche"
