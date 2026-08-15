"""The orchestrator's seam to the agents.

Separate from `trustrail.ports` because this is a different kind of boundary.
Those are infrastructure — a clock, a key, a table — and each has an in-memory
twin and an AWS one. This one is workstream B, which is a *separate deployable
on purpose*: the agents are assumed compromisable, so they do not share a
process with the thing that assembles what the Verifier sees.

Expressing it as a protocol is what keeps that true. `trustrail` never imports
workstream B; B supplies an adapter, in-process for the demo and over HTTP when
the agents run on their own. The orchestrator cannot tell the difference, which
is the point — it was never entitled to trust either of them anyway.

**Why one port and not two.** CLAUDE.md describes the Browser Agent and the
Evaluator Agent as two agents, and they are: two loops, two responsibilities,
and — the part that matters — two different keys, because the Evaluator signs
its findings and the Browser cannot. That separation lives inside B, where both
agents actually run. What crosses into `trustrail` is one round trip to one
service, returning what was chosen and what the Evaluator concluded about it.
Splitting it into two calls here would mean sending B's own listing back to B,
and would make this package define a model of a merchant listing that nothing
in it ever reads.

None of the trust rests on B's honesty. The Evaluator's signature is checked
against the Agent Registry, its subject is checked against the charge, and every
deterministic check runs whatever the evidence says.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trustrail.models.candidate import PurchaseCandidate
from trustrail.models.evaluation import SignedEvaluatorOutput
from trustrail.models.money import Money


@dataclass(frozen=True, slots=True)
class ShoppingResult:
    """What B came back with: a product, and its signed opinion of that product."""

    candidate: PurchaseCandidate
    evaluation: SignedEvaluatorOutput


class ShoppingAgents(Protocol):
    """Workstream B: find something to buy, and judge what was found.

    The implementation must sign the evaluation with the key registered for the
    id it stamps on its output, and must fill in the subject (mandate, basket,
    amount). Without the subject the signature buys nothing — a genuine
    low-risk evaluation of a S$4 toothbrush could be replayed onto a S$4000
    gift card.
    """

    async def shop(
        self, *, intent: str, max_amount: Money, mandate_id: str
    ) -> ShoppingResult:
        """Select a candidate for `intent` and return it with signed evidence.

        Raises `NoCandidate` when no merchant offered anything. Returning a
        candidate that is over the cap, or a poisoned one, is *not* an error
        here: the Verifier catches both, and an agent that could suppress its
        own violations by declining to report them would be a worse design.
        """
        ...


class NoCandidate(LookupError):
    """No merchant offered anything matching the intent."""
