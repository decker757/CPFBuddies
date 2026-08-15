"""Assembling the chain side: one place that knows how the pieces connect.

Everything here is construction, no policy. It exists so that the composition
roots — the demo app, the scripts, a deployed worker — do not each grow their
own slightly different idea of how to reach the contract.

The two keys are separate parameters because they are separate keys. The
contract splits REGISTRAR_ROLE from SETTLER_ROLE so that a compromised
settlement worker cannot invent mandates of its own, and passing one signer for
both would quietly undo that at the wiring layer, where nobody would look for
it. Passing the same signer twice is possible and is a deployment choice, not a
default this module offers.

Both are `Signer`s rather than keys: `LocalSigner` for a local node,
`KmsSigner` in a deployment, and this module cannot tell which it has.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from web3 import Web3

from trustrail.ports import Signer
from trustrail.settlement.chain.deployment import Deployment, load_deployment
from trustrail.settlement.chain.registrar import ChainMandateRegistrar
from trustrail.settlement.chain.registry_client import MandateRegistryClient
from trustrail.settlement.chain.token import TokenClient
from trustrail.settlement.rails.x402_onchain import X402OnchainRail


@dataclass(frozen=True)
class ChainWiring:
    """Everything the rail needs to reach one deployed MandateRegistry."""

    deployment: Deployment
    w3: Web3
    registrar: ChainMandateRegistrar
    rail: X402OnchainRail
    token: TokenClient
    registry: MandateRegistryClient
    """Read access to the contract: `get_mandate`, `is_spendable`, `preflight_spend`.

    Bound to the settler's key, which matters only for the `from` address on a
    simulated call. Anything that writes goes through `registrar` or `rail`, so
    that role separation is not something a caller can reach around.
    """

    @property
    def registry_address(self) -> str:
        """The contract a buyer must approve before any purchase can settle."""
        return self.registry.address


def build_chain(
    *,
    rpc_url: str,
    network: str,
    registrar_signer: Signer,
    settler_signer: Signer,
    deployments_dir: Path | None = None,
) -> ChainWiring:
    """Connect to `network` and assemble the registrar and the settlement rail.

    Addresses come from `onchain/deployments/<network>.json`, written by the
    Hardhat deploy script — never from Python config, so there is one source of
    truth and no chance of settling against a registry nobody deployed.

    `load_deployment` also asserts the deployed token's decimals match the wire
    contract's, so a token that disagrees fails here rather than settling an
    amount that is wrong by a factor of ten to some power.
    """
    deployment = load_deployment(network, deployments_dir)
    w3 = Web3(Web3.HTTPProvider(rpc_url))

    registry_for_registrar = MandateRegistryClient(w3, deployment, registrar_signer)
    registry_for_settler = MandateRegistryClient(w3, deployment, settler_signer)
    token = TokenClient(w3, deployment.settlement_token, deployment.chain_id)

    return ChainWiring(
        deployment=deployment,
        w3=w3,
        registrar=ChainMandateRegistrar(registry_for_registrar),
        rail=X402OnchainRail(registry_for_settler, deployment.chain_id, token),
        token=token,
        registry=registry_for_settler,
    )
