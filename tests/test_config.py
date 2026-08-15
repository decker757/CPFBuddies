"""The shipped config file must load and mean what the code assumes."""

from __future__ import annotations

from pathlib import Path

import pytest

from trustrail.models.money import Currency
from trustrail.signing.eip712 import FUJI_CHAIN_ID
from trustrail.verifier.config import VerifierConfig

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "verifier.toml"


@pytest.fixture
def shipped() -> VerifierConfig:
    return VerifierConfig.from_toml(CONFIG_PATH)


def test_the_shipped_config_parses(shipped: VerifierConfig) -> None:
    assert shipped.pass_score_max == 3
    assert shipped.review_score_max == 7


def test_the_rail_settles_in_xsgd(shipped: VerifierConfig) -> None:
    """A track rule, so it belongs in a test rather than in a comment."""
    assert shipped.settlement_currency is Currency.XSGD


def test_the_domain_points_at_a_testnet_until_someone_moves_it(
    shipped: VerifierConfig,
) -> None:
    """CLAUDE.md: deploy and test on Fuji first, touch mainnet only after."""
    assert shipped.domain.chain_id == FUJI_CHAIN_ID


def test_environment_variables_override_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deployments set the KMS issuer address without editing a committed file."""
    monkeypatch.setenv("TRUSTRAIL_ISSUER_ADDRESS", "0x" + "ab" * 20)

    assert VerifierConfig.from_toml(CONFIG_PATH).issuer_address == "0x" + "ab" * 20


def test_the_config_fingerprint_is_stable_across_loads(
    shipped: VerifierConfig,
) -> None:
    """Otherwise `config_version` on a verdict would be meaningless."""
    assert shipped.version == VerifierConfig.from_toml(CONFIG_PATH).version
