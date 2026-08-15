"""The primary rail: XSGD on Avalanche C-Chain via MandateRegistry.

CLAUDE.md's open decision defaults to x402 primary, because settling onchain is what the
Avalanche prize rewards and what makes the revert demo possible.

The contract re-validates every constraint the Verifier already checked. That is not
redundancy -- it is the reason the claim "you do not have to trust our services" holds.
"""

from __future__ import annotations

import logging

from trustrail.models.audit import SettlementOutcome
from trustrail.models.money import Currency
from trustrail.settlement.chain.explorer import transaction_url
from trustrail.settlement.chain.registry_client import MandateRegistryClient
from trustrail.settlement.chain.token import TokenClient
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

    def __init__(
        self,
        client: MandateRegistryClient,
        chain_id: int,
        token: TokenClient | None = None,
    ) -> None:
        """`token` enables the funding preflight; omit it to go straight to the chain.

        The preflight is two `eth_call`s and it exists because the failure it
        catches is otherwise illegible. `spend` ends in `transferFrom`, so an
        unfunded buyer -- or one who never granted the allowance -- produces a
        bare ERC-20 revert with no custom error, which decodes to "unknown
        revert" on the dashboard. That is the single most likely thing to go
        wrong on demo day and the least informative message to see when it does.
        """
        self._client = client
        self._chain_id = chain_id
        self._token = token

    @property
    def name(self) -> str:
        return RAIL_NAME

    def settle(self, instruction: SettlementInstruction) -> SettlementReceipt:
        if instruction.amount.currency is not Currency.XSGD:
            raise UnsettleableCurrency(
                f"{instruction.amount.currency} cannot settle onchain; only XSGD can"
            )

        unfunded = self._funding_problem(instruction)
        if unfunded is not None:
            # REFUSED, not ERROR: the rail is working and the money is not
            # there. Retrying on a timer will not conjure a balance, and the
            # buyer has to act.
            return SettlementReceipt(
                mandate_id=instruction.mandate_id,
                rail=self.name,
                status=SettlementOutcome.REFUSED,
                detail=unfunded,
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
        except Exception as error:
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

    def _funding_problem(self, instruction: SettlementInstruction) -> str | None:
        """Why `transferFrom` would fail on the buyer's side, in plain words.

        None means the buyer is good for it. This checks the principal, not the
        settler: the settler pays gas, the principal pays the merchant.
        """
        if self._token is None:
            return None

        needed = instruction.amount.minor_units
        principal = instruction.principal

        balance = self._token.balance_of(principal)
        if balance < needed:
            return (
                f"principal {principal} holds {balance} of the {needed} minor "
                f"units required; top up the wallet"
            )

        allowance = self._token.allowance(principal, self._client.address)
        if allowance < needed:
            return (
                f"principal {principal} has approved MandateRegistry for "
                f"{allowance} of the {needed} minor units required; the buyer "
                f"must call approve() on the token"
            )
        return None
