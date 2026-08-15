"""The composite services: the two orchestrators.

Everything else in this package is atomic — the Mandate Service only signs, the
Verifier only decides, the registries only remember. Orchestration is where
those get sequenced into a purchase, and keeping it in its own package is what
stops that sequencing leaking back into the pieces it coordinates.

The Verifier in particular stays a pure function precisely because the
orchestrator does every lookup on its behalf.
"""

from trustrail.orchestrator.onboarding import OnboardingOrchestrator
from trustrail.orchestrator.ports import (
    NoCandidate,
    ShoppingAgents,
    ShoppingResult,
)
from trustrail.orchestrator.purchase import (
    PurchaseOrchestrator,
    PurchaseOutcome,
    ReviewNotFound,
    ReviewNotPending,
)

__all__ = [
    "NoCandidate",
    "OnboardingOrchestrator",
    "PurchaseOrchestrator",
    "PurchaseOutcome",
    "ReviewNotFound",
    "ReviewNotPending",
    "ShoppingAgents",
    "ShoppingResult",
]
