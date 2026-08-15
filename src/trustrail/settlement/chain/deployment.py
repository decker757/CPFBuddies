"""Loading deployed addresses and ABIs.

Addresses come from ``onchain/deployments/<network>.json``, written by the Hardhat deploy
script. That file is the single source of truth; never copy an address into Python config by
hand.

``onchain/`` holds the Solidity project. ``contracts/`` in this repo means something else
entirely -- the JSON wire contract shared between workstreams.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from trustrail.models.money import CURRENCY_DECIMALS, Currency
from trustrail.models.primitives import HexAddress

REPO_ROOT = Path(__file__).resolve().parents[4]
DEPLOYMENTS_DIR = REPO_ROOT / "onchain" / "deployments"
ARTIFACTS_DIR = REPO_ROOT / "onchain" / "artifacts" / "contracts"


class TokenDecimalsMismatch(RuntimeError):
    """The deployed token disagrees with what the wire contract assumes.

    ``CURRENCY_DECIMALS`` fixes XSGD at 6 and its comment asks track C to confirm that against
    the deployed token before mainnet. This is that confirmation, made loud: if the two ever
    disagree, every amount in the system is wrong by a factor of ten to some power, and
    failing at startup beats settling the wrong number.
    """


class Deployment(BaseModel):
    """One network's deployed addresses, as written by ``scripts/deploy.ts``."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    network: str
    chain_id: int = Field(alias="chainId")
    settlement_token: HexAddress = Field(alias="settlementToken")
    settlement_token_symbol: str = Field(alias="settlementTokenSymbol")
    settlement_token_decimals: int = Field(alias="settlementTokenDecimals", ge=0, le=36)
    mandate_registry: HexAddress = Field(alias="mandateRegistry")
    registrar: HexAddress
    settler: HexAddress

    def assert_decimals_match(self, currency: Currency = Currency.XSGD) -> None:
        """Fail loudly if the deployed token's decimals differ from the wire contract's."""
        expected = CURRENCY_DECIMALS[currency]
        if self.settlement_token_decimals != expected:
            raise TokenDecimalsMismatch(
                f"{self.network}: {self.settlement_token_symbol} at "
                f"{self.settlement_token} reports {self.settlement_token_decimals} decimals, "
                f"but CURRENCY_DECIMALS[{currency}] is {expected}"
            )


def load_deployment(network: str, deployments_dir: Path | None = None) -> Deployment:
    """Read the deployment record for a network and check it against the wire contract."""
    directory = deployments_dir or DEPLOYMENTS_DIR
    path = directory / f"{network}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"no deployment for '{network}' at {path}. Run the Hardhat deploy script first."
        )
    deployment = Deployment.model_validate_json(path.read_text(encoding="utf-8"))
    deployment.assert_decimals_match()
    return deployment


def load_abi(contract_name: str, artifacts_dir: Path | None = None) -> list[dict]:
    """Read a contract ABI straight from the Hardhat build output.

    Reading the compiler's artifact rather than a copied-out ABI means the Python side cannot
    drift from the deployed bytecode without the build noticing.
    """
    directory = artifacts_dir or ARTIFACTS_DIR
    matches = list(directory.rglob(f"{contract_name}.json"))
    if not matches:
        raise FileNotFoundError(
            f"no artifact for '{contract_name}' under {directory}. Run 'npx hardhat compile'."
        )
    artifact = json.loads(matches[0].read_text(encoding="utf-8"))
    return artifact["abi"]
