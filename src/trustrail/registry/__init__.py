"""The two registries: who may be paid, and whose evidence counts.

There is no service class here, only the HTTP surface. The registries are
lookup tables — `MerchantDirectory` and `AgentDirectory` in `trustrail.ports`
are the whole interface, the Onboarding Orchestrator does the writes, and the
Verifier's caller does the reads. A service layer that only forwarded calls
would be a layer nobody could point at a reason for.
"""

from trustrail.registry.api import build_router

__all__ = ["build_router"]
