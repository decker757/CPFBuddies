from fastapi import FastAPI

from app.marketplace.repository import (
    InMemoryQuoteRepository,
    SecureQuoteIdGenerator,
    SystemClock,
)
from app.marketplace.routes import create_marketplace_router
from app.marketplace.service import MarketplaceService, SettlementProfile


def build_marketplace_service(
    settlement: SettlementProfile | None = None,
) -> MarketplaceService:
    """The stub marketplace.

    `settlement` decides which chain and token its 402s quote. Left out, the
    profile's defaults describe nothing deployed — fine for the offline suite,
    wrong for anything that settles, which is why `app.rail` passes one derived
    from the deployment record.
    """
    return MarketplaceService(
        quotes=InMemoryQuoteRepository(),
        clock=SystemClock(),
        quote_ids=SecureQuoteIdGenerator(),
        settlement=settlement or SettlementProfile(),
    )


def create_app(marketplace: MarketplaceService | None = None) -> FastAPI:
    application = FastAPI(
        title="CPF Buddies Workstream B",
        version="0.1.0",
        description="Merchant surface and internal candidate-evaluation agents.",
    )
    application.include_router(
        create_marketplace_router(marketplace or build_marketplace_service())
    )

    @application.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
