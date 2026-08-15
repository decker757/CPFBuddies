"""The shipped config file must load and mean what the code assumes."""

from __future__ import annotations

from pathlib import Path

import pytest

from trustrail.models.money import Currency
from trustrail.models.primitives import ZERO_ADDRESS
from trustrail.settlement.chain.deployment import load_deployment
from trustrail.signing.eip712 import AVALANCHE_MAINNET_CHAIN_ID, Eip712Domain
from trustrail.verifier.config import VerifierConfig

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "verifier.toml"

#: The deployed MandateRegistry on Avalanche C-Chain mainnet.
MANDATE_REGISTRY = "0xdb4050cf28cfa0cb956bfdbcb64341ee1c592c23"


@pytest.fixture
def shipped() -> VerifierConfig:
    return VerifierConfig.from_toml(CONFIG_PATH)


def test_the_shipped_config_parses(shipped: VerifierConfig) -> None:
    assert shipped.pass_score_max == 3
    assert shipped.review_score_max == 7


def test_the_rail_settles_in_xsgd(shipped: VerifierConfig) -> None:
    """A track rule, so it belongs in a test rather than in a comment."""
    assert shipped.settlement_currency is Currency.XSGD


def test_the_domain_points_at_the_deployed_registry(shipped: VerifierConfig) -> None:
    """Cut over to mainnet on 2026-08-15. This test moved with it.

    It used to assert Fuji, on the grounds that touching mainnet should be a
    deliberate act. It is now the other half of that guarantee: the domain must
    name a *real* contract, because a zero `verifying_contract` in production
    means mandates are signed under a domain no deployed contract shares.
    """
    assert shipped.domain.chain_id == AVALANCHE_MAINNET_CHAIN_ID
    assert shipped.domain.verifying_contract != ZERO_ADDRESS
    assert shipped.domain.verifying_contract == MANDATE_REGISTRY


def test_the_config_domain_matches_the_deployment_record(
    shipped: VerifierConfig,
) -> None:
    """The two places an address can disagree, checked against each other.

    `config/verifier.toml` is what the Verifier trusts; the deployment record is
    what the settlement worker calls. If they drift, mandates verify offchain
    and then hit a contract that never heard of them.
    """
    deployment = load_deployment("avalanche")

    assert shipped.domain.verifying_contract == deployment.mandate_registry
    assert shipped.domain.chain_id == deployment.chain_id


def test_environment_variables_override_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deployments set the KMS issuer address without editing a committed file."""
    monkeypatch.setenv("TRUSTRAIL_ISSUER_ADDRESS", "0x" + "ab" * 20)

    assert VerifierConfig.from_toml(CONFIG_PATH).issuer_address == "0x" + "ab" * 20


def test_the_committed_fixtures_do_not_depend_on_the_deployed_domain() -> None:
    """Moving the domain at cutover is config, not a fixture regeneration.

    The fixtures are generated under `Eip712Domain()`'s defaults, so pointing a
    deployment at Fuji or mainnet leaves `contracts/` untouched. This is worth
    pinning because believing otherwise turns a one-line config change into a
    feared coordinated commit that nobody wants to do at 2am.

    What the fixtures prove is the decision logic, not the deployment. The
    deployment risk is the *next* test.
    """
    from trustrail.contracts.scenarios import ScenarioBuilder, demo_config

    assert ScenarioBuilder().domain == Eip712Domain()
    assert demo_config().domain == Eip712Domain()


def test_a_mandate_signed_under_one_domain_fails_under_another(build) -> None:
    """The failure mode a cutover actually has.

    The Mandate Service signs under a domain and the Verifier checks under one.
    If a deployment moves one and not the other, every mandate fails its
    signature — not with a helpful message about configuration, but as
    MANDATE_DIGEST_MISMATCH, which reads like tampering. Wire both from the
    same place.
    """
    from trustrail.contracts.keys import ISSUER_ADDRESS
    from trustrail.models.verdict import Decision, ReasonCode
    from trustrail.verifier.service import VerifierService

    mainnet = Eip712Domain(chain_id=43114, verifying_contract="0x" + "cd" * 20)
    verifier = VerifierService(
        VerifierConfig(issuer_address=ISSUER_ADDRESS, domain=mainnet)
    )

    verdict = verifier.verify(build.request())

    assert verdict.decision is Decision.FAIL
    assert ReasonCode.MANDATE_DIGEST_MISMATCH in verdict.reason_codes


def test_the_config_fingerprint_is_stable_across_loads(
    shipped: VerifierConfig,
) -> None:
    """Otherwise `config_version` on a verdict would be meaningless."""
    assert shipped.version == VerifierConfig.from_toml(CONFIG_PATH).version
