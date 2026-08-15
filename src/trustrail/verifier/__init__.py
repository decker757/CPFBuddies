"""The Verifier Service: PASS, REVIEW or FAIL, and nothing else.

No network calls, no side effects, no state. Everything it needs arrives in the
request, which is why the interesting cases — expired mandates, forged
evaluator verdicts, a charge one cent over the cap — are ordinary unit tests.
"""

from trustrail.verifier.checks import (
    DETERMINISTIC_CHECKS,
    JUDGEMENT_CHECKS,
    Rejection,
    VerificationContext,
)
from trustrail.verifier.config import VerifierConfig
from trustrail.verifier.service import VerifierService

__all__ = [
    "DETERMINISTIC_CHECKS",
    "JUDGEMENT_CHECKS",
    "Rejection",
    "VerificationContext",
    "VerifierConfig",
    "VerifierService",
]
