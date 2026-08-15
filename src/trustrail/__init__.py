"""TrustRail: a trust rail for agent payments.

We do not trust the agent, we trust the rail. Enforcement happens outside the
agent, at settlement, and is publicly verifiable onchain.

This package is workstream A: the Mandate Service (issues and signs mandates)
and the Verifier Service (decides whether a charge may settle against one).
"""

__version__ = "0.1.0"
