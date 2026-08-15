"""Wires the services into one FastAPI app.

Two factories. `create_app` takes services you built, which is what deployment
and tests use. `create_demo_app` builds the offline stack — a local key, stores
in memory — so the whole rail runs with no AWS account and no network.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from trustrail import __version__
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
    *, mandates: MandateService, verifier: VerifierService
) -> FastAPI:
    app = FastAPI(
        title="TrustRail",
        version=__version__,
        summary="Mandate issuance and settlement verification for agent payments.",
    )
    app.include_router(mandate_api.build_router(mandates))
    app.include_router(verifier_api.build_router(verifier, mandates))

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
    mandates = MandateService(
        signer=signer,
        store=InMemoryMandateStore(),
        kill_switch=InMemoryKillSwitchStore(),
        audit=InMemoryAuditLog(),
        domain=domain,
    )
    verifier = VerifierService(
        VerifierConfig(
            issuer_address=signer.address, domain=domain or Eip712Domain()
        )
    )
    return create_app(mandates=mandates, verifier=verifier)
