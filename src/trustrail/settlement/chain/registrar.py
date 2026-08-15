"""Putting mandates on the ledger at mint time.

This is the `MandateRegistrar` port backed by the real contract. It exists so
`trustrail.mandate.service` can register a mandate onchain without importing
web3 or knowing what a transaction is.

**Why registration happens at mint and not at settlement.** The contract is the
authority — `spend` re-checks the cap, the merchant, the expiry and one-time
consumption independently of anything offchain. A mandate the contract has
never seen gets none of that; `spend` would simply revert `MandateNotFound`.
Registering at mint also keeps REGISTRAR_ROLE and SETTLER_ROLE in different
hands, which is what makes "a compromised settlement worker still cannot exceed
the cap" a fact about the deployment rather than a claim about our code.

The cost is a transaction per mint, including mints that go on to FAIL
evaluation. That is the deliberate trade: the mandate is publicly verifiable
from the moment the human approved it, which is also the better demo.
"""

from __future__ import annotations

import logging
from datetime import datetime

from trustrail.errors import MandateRegistrationFailed
from trustrail.settlement.chain.registry_client import MandateRegistryClient

logger = logging.getLogger(__name__)


class ChainMandateRegistrar:
    """Registers and revokes mandates on MandateRegistry. Holds REGISTRAR_ROLE."""

    def __init__(self, client: MandateRegistryClient) -> None:
        self._client = client

    def register(
        self,
        *,
        mandate_id: str,
        principal: str,
        agent_address: str,
        cap_minor_units: int,
        expires_at: datetime,
        digest: str,
    ) -> str | None:
        result = self._client.register_mandate(
            mandate_id=mandate_id,
            principal=principal,
            agent=agent_address,
            # No merchant at mint: the buyer approved a budget and an intent,
            # not a SKU. The contract takes address(0) and binds on first spend.
            merchant=None,
            cap=cap_minor_units,
            # Solidity wants uint64 unix seconds. `Timestamp` is timezone-aware
            # at the edge, so this conversion cannot pick up a local offset.
            expires_at=int(expires_at.timestamp()),
            mandate_digest=digest,
        )
        if not result.confirmed:
            raise MandateRegistrationFailed(
                f"could not register mandate {mandate_id} onchain: {result.revert}"
            )
        logger.info(
            "mandate registered onchain",
            extra={"mandate_id": mandate_id, "tx_hash": result.tx_hash},
        )
        return result.tx_hash

    def revoke(self, mandate_id: str) -> str | None:
        result = self._client.revoke(mandate_id)
        if not result.confirmed:
            # Revocation failing is not fatal the way registration is. The
            # offchain record is already REVOKED, and the Verifier reads that,
            # so nothing can settle through this system regardless. What is
            # lost is the onchain guarantee, so it is logged loudly rather than
            # raised into a human's face while they are killing a purchase.
            logger.error(
                "onchain revocation failed; mandate is revoked offchain only",
                extra={"mandate_id": mandate_id, "revert": str(result.revert)},
            )
            return None
        return result.tx_hash
