"""The primary rail: XSGD on Avalanche C-Chain via MandateRegistry.

CLAUDE.md's open decision defaults to x402 primary, because settling onchain is what the
Avalanche prize rewards and what makes the revert demo possible.

The contract re-validates every constraint the Verifier already checked. That is not
redundancy -- it is the reason the claim "you do not have to trust our services" holds.
"""

from __future__ import annotations

import logging

from trustrail.models.money import Currency
from trustrail.settlement.chain.explorer import transaction_url
from trustrail.settlement.chain.registry_client import MandateRegistryClient
from trustrail.models.audit import SettlementOutcome
from trustrail.settlement.models import SettlementInstruction, SettlementReceipt

logger = logging.getLogger(__name__)

RAIL_NAME = "x402-onchain"


class UnsettleableCurrency(ValueError):
    """The instruction is denominated in something this rail cannot move.

    Only XSGD settles onchain -- a track rule. The Verifier rejects anything else with
    CURRENCY_NOT_SETTLEABLE long before here, so reaching this means the pipeline was skipped.
    """


class X402OnchainRail:
    """Settles by calling ``MandateRegistry.spend`` and reporting what the chain did."""

    def __init__(self, client: MandateRegistryClient, chain_id: int) -> None:
        self._client = client
        self._chain_id = chain_id

    @property
    def name(self) -> str:
        return RAIL_NAME

    def settle(self, instruction: SettlementInstruction) -> SettlementReceipt:
        if instruction.amount.currency is not Currency.XSGD:
            raise UnsettleableCurrency(
                f"{instruction.amount.currency} cannot settle onchain; only XSGD can"
            )

        try:
            result = self._client.spend(
                mandate_id=instruction.mandate_id,
                merchant=instruction.payout_address,
                # Minor units come from the wire contract's own conversion, and the deployment
                # loader has already asserted the deployed token agrees with it.
                amount=instruction.amount.minor_units,
                basket_hash=instruction.basket_hash,
            )
        except Exception as error:  # noqa: BLE001 - RPC and nonce faults are retryable
            logger.exception("settlement transport failed for %s", instruction.mandate_id)
            return SettlementReceipt(
                mandate_id=instruction.mandate_id,
                rail=self.name,
                status=SettlementOutcome.ERROR,
                detail=f"{type(error).__name__}: {error}",
            )

        # No hash when the node refused to broadcast a doomed transaction at all.
        explorer = (
            transaction_url(self._chain_id, result.tx_hash) if result.tx_hash else None
        )

        if result.confirmed:
            return SettlementReceipt(
                mandate_id=instruction.mandate_id,
                rail=self.name,
                status=SettlementOutcome.SETTLED,
                reference=result.tx_hash,
                explorer_url=explorer,
            )

        # A revert is the contract working. Report it as a refusal, with the hash, because
        # showing the reverted transaction on Snowtrace is the point.
        revert = result.revert
        return SettlementReceipt(
            mandate_id=instruction.mandate_id,
            rail=self.name,
            status=SettlementOutcome.REFUSED,
            reference=result.tx_hash,
            explorer_url=explorer,
            reason_code=revert.reason_code if revert else None,
            detail=str(revert) if revert else "reverted",
        )
