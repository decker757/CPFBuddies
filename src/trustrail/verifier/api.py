"""HTTP surface for the Verifier Service.

One endpoint, and it takes everything it needs in the body — including the
mandate state, the merchant record and the current time. That looks unusual for
an HTTP service, and it is the point: the Verifier does not look anything up,
so it cannot be steered by what it finds. Assembling the request is the Purchase
Orchestrator's job (workstream D).

The verdict is written to the audit trail here rather than inside the Verifier,
which has no side effects. Same reason.
"""

from __future__ import annotations

from fastapi import APIRouter

from trustrail.mandate.service import MandateService
from trustrail.models.verdict import Verdict
from trustrail.models.verification import VerificationRequest
from trustrail.verifier.service import VerifierService


def build_router(verifier: VerifierService, mandates: MandateService) -> APIRouter:
    router = APIRouter(tags=["verifier"])

    @router.post("/verify")
    def verify(request: VerificationRequest) -> Verdict:
        verdict = verifier.verify(request)
        mandates.record_verdict(verdict)
        return verdict

    return router
