from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.contracts import (
    ListingsResponse,
    MarketplaceErrorResponse,
    PaymentRequired,
    PurchaseReceipt,
    PurchaseRequest,
)
from trustrail.x402.terms import PAYMENT_HEADER, decode_proof

from app.marketplace.service import (
    ExpiredQuote,
    MarketplaceService,
    OutOfStock,
    QuoteAlreadyConsumed,
    QuoteIntegrityFailure,
    SkuNotInQuote,
    UnknownQuote,
)


def create_marketplace_router(marketplace: MarketplaceService) -> APIRouter:
    router = APIRouter(tags=["marketplace"])

    @router.get("/listings", response_model=ListingsResponse)
    def get_listings(
        q: Annotated[str | None, Query(max_length=200)] = None,
        max_price: Annotated[Decimal | None, Query(gt=0, le=1_000_000_000)] = None,
        currency: Annotated[Literal["XSGD"], Query()] = "XSGD",
    ) -> ListingsResponse:
        return marketplace.list_items(q=q, max_price=max_price, currency=currency)

    @router.post(
        "/purchase",
        response_model=PurchaseReceipt,
        responses={
            400: {"model": MarketplaceErrorResponse},
            402: {"model": PaymentRequired},
            404: {"model": MarketplaceErrorResponse},
            409: {"model": MarketplaceErrorResponse},
            410: {"model": MarketplaceErrorResponse},
        },
    )
    def purchase(
        request: PurchaseRequest,
        x_payment: str | None = Header(
            default=None, alias=PAYMENT_HEADER, min_length=1, max_length=4_000
        ),
    ) -> PurchaseReceipt | JSONResponse:
        # The header carries workstream C's PaymentProof, base64 of canonical JSON. Decoding it
        # here means a malformed or truncated proof is a 400 rather than a receipt issued
        # against something we never read.
        reference: str | None = None
        if x_payment is not None:
            try:
                reference = decode_proof(x_payment).reference
            except ValueError as error:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_payment_proof"
                ) from error

        try:
            result = marketplace.purchase(request, reference)
        except UnknownQuote as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error.code) from error
        except ExpiredQuote as error:
            raise HTTPException(status_code=status.HTTP_410_GONE, detail=error.code) from error
        except SkuNotInQuote as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=error.code
            ) from error
        except (OutOfStock, QuoteAlreadyConsumed, QuoteIntegrityFailure) as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.code) from error

        if isinstance(result, PaymentRequired):
            return JSONResponse(status_code=402, content=result.model_dump(mode="json"))
        return result

    return router
