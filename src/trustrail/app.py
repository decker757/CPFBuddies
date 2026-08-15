"""Wires the services into one FastAPI app.

Two factories. `create_app` takes services you built, which is what deployment
and tests use. `create_demo_app` builds the offline stack — a local key, stores
in memory — so the whole rail runs with no AWS account and no network.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from trustrail import __version__
from trustrail.audit import api as audit_api
from trustrail.errors import (
    IllegalBinding,
    MandateAlreadyExists,
    MandateNotFound,
    MandateStatusConflict,
    NonceAlreadyClaimed,
    TrustRailError,
)
from trustrail.mandate import api as mandate_api
from trustrail.mandate.service import MandateService
from trustrail.orchestrator import api as orchestrator_api
from trustrail.orchestrator.onboarding import OnboardingOrchestrator
from trustrail.orchestrator.purchase import PurchaseOrchestrator
from trustrail.ports import AgentDirectory, AuditLog, MerchantDirectory
from trustrail.registry import api as registry_api
from trustrail.signing.eip712 import Eip712Domain
from trustrail.signing.local import LocalSigner
from trustrail.stores.memory import (
    InMemoryAuditLog,
    InMemoryKillSwitchStore,
    InMemoryMandateStore,
)
from trustrail.verifier import api as verifier_api
from trustrail.verifier.config import VerifierConfig
from trustrail.verifier.service import VerifierService

#: How each deliberate error surfaces over HTTP. Conflicts are 409 because they
#: all mean the same thing to a caller: somebody else got there first.
_STATUS_FOR_ERROR: dict[type[TrustRailError], int] = {
    MandateNotFound: 404,
    MandateAlreadyExists: 409,
    NonceAlreadyClaimed: 409,
    MandateStatusConflict: 409,
    IllegalBinding: 409,
}
_DEFAULT_ERROR_STATUS = 400


def create_app(
    *,
    mandates: MandateService,
    verifier: VerifierService,
    orchestrator: PurchaseOrchestrator | None = None,
    onboarding: OnboardingOrchestrator | None = None,
    merchants: MerchantDirectory | None = None,
    agents: AgentDirectory | None = None,
    audit: AuditLog | None = None,
    cors_origins: list[str] | None = None,
    lifespan: Any | None = None,
) -> FastAPI:
    """Mount whichever services this deployment runs.

    The composite services are optional because the atomic ones can be deployed
    without them — and because the Purchase Orchestrator needs workstream B's
    agents, which live in a separate installable. `backend.app.rail` is the
    composition root that has both and wires the whole thing together.

    `audit` is the log itself rather than a service, because the dashboard feed
    reads across every mandate and no service owns that view. Omit it and the
    feed is simply not mounted — a deployment that streams from CloudWatch
    instead should not be made to serve a duplicate of it over HTTP.

    `cors_origins` is an explicit list, never a wildcard, and defaults to none.
    The frontend runs on its own dev server, so without this every request from
    it fails in the browser — but this API has no authentication at all, so the
    origins that may call it are worth naming rather than opening to anyone who
    finds the URL.

    `lifespan` is how the Settlement Worker gets to run beside these routes. In
    deployment it is its own process consuming SQS, and this parameter is what a
    single-process demo uses instead — without it a PASS reaches the queue and
    stays there, because nothing else in this package ever drains one.
    """
    app = FastAPI(
        title="TrustRail",
        version=__version__,
        summary="Mandate issuance and settlement verification for agent payments.",
        lifespan=lifespan,
    )
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Last-Event-ID"],
        )
    app.include_router(mandate_api.build_router(mandates))
    app.include_router(verifier_api.build_router(verifier, mandates))
    if orchestrator is not None:
        app.include_router(orchestrator_api.build_router(orchestrator))
    if onboarding is not None and merchants is not None and agents is not None:
        app.include_router(
            registry_api.build_router(
                onboarding=onboarding, merchants=merchants, agents=agents
            )
        )
    if audit is not None:
        app.include_router(audit_api.build_router(audit))

    @app.exception_handler(TrustRailError)
    def handle_trustrail_error(_: Request, exc: TrustRailError) -> JSONResponse:
        status_code = _STATUS_FOR_ERROR.get(type(exc), _DEFAULT_ERROR_STATUS)
        return JSONResponse(
            status_code=status_code,
            content={"error": type(exc).__name__, "detail": str(exc)},
        )

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok", "issuer": mandates.issuer_address}

    return app


def create_demo_app(domain: Eip712Domain | None = None) -> FastAPI:
    """The whole rail, offline: a local issuer key and in-memory stores.

    The Verifier is configured with the address of the signer the Mandate
    Service actually uses, which is the same relationship production has — the
    Verifier trusts an address, never a key.
    """
    signer = LocalSigner.generate()
    audit = InMemoryAuditLog()
    mandates = MandateService(
        signer=signer,
        store=InMemoryMandateStore(),
        kill_switch=InMemoryKillSwitchStore(),
        audit=audit,
        domain=domain,
    )
    verifier = VerifierService(
        VerifierConfig(
            issuer_address=signer.address, domain=domain or Eip712Domain()
        )
    )
    return create_app(mandates=mandates, verifier=verifier, audit=audit)
