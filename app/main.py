"""FastAPI entrypoint for GDG Antigravity Bank."""

from pathlib import Path

from fastapi import FastAPI, Header
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt

from app.db import get_db_path
from app.fraud import FraudLimitExceededError
from app.ledger import (
    AccountNotFoundError,
    DuplicateIdempotencyKeyError,
    InsufficientFundsError,
    InvalidTransferError,
    execute_transfer,
    list_accounts,
    list_ledger_entries,
    list_transfers,
)
from seed_db import init_db

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="GDG Antigravity Bank", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class TransferRequest(BaseModel):
    """Validated transfer request payload using strict integer cents."""

    from_account_id: str
    to_account_id: str
    amount_cents: StrictInt = Field(..., gt=0)


@app.get("/")
def serve_dashboard() -> FileResponse:
    """Serve the single-page banking dashboard."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok"}


@app.get("/api/accounts")
def api_list_accounts() -> dict:
    """Return current accounts and balances."""
    return {"accounts": list_accounts()}


@app.get("/api/transfers")
def api_list_transfers() -> dict:
    """Return recent transfers and blocked fraud audit events."""
    return {"transfers": list_transfers()}


@app.get("/api/ledger")
def api_list_ledger() -> dict:
    """Return double-entry debit and credit ledger rows."""
    return {"entries": list_ledger_entries()}


@app.post("/api/transfers", status_code=201)
def api_create_transfer(
    payload: TransferRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Execute an idempotent, fraud-checked P2P transfer."""
    if idempotency_key is None or not idempotency_key.strip():
        return JSONResponse(
            status_code=400,
            content={
                "error": "MISSING_IDEMPOTENCY_KEY",
                "message": "Idempotency-Key header is required.",
            },
        )

    try:
        result = execute_transfer(
            from_account_id=payload.from_account_id,
            to_account_id=payload.to_account_id,
            amount_cents=payload.amount_cents,
            idempotency_key=idempotency_key,
        )
        return JSONResponse(status_code=201, content=result)
    except FraudLimitExceededError as err:
        return JSONResponse(
            status_code=403,
            content={
                "error": err.code,
                "message": err.message,
                "amount_cents": err.amount_cents,
                "limit_cents": err.limit_cents,
            },
        )
    except InsufficientFundsError as err:
        return JSONResponse(
            status_code=400,
            content={"error": "INSUFFICIENT_FUNDS", "message": str(err)},
        )
    except DuplicateIdempotencyKeyError as err:
        return JSONResponse(
            status_code=409,
            content={
                "error": "DUPLICATE_IDEMPOTENCY_KEY",
                "message": str(err),
                "existing_transfer": err.existing_transfer,
            },
        )
    except AccountNotFoundError as err:
        return JSONResponse(
            status_code=404,
            content={"error": "ACCOUNT_NOT_FOUND", "message": str(err)},
        )
    except InvalidTransferError as err:
        return JSONResponse(
            status_code=400,
            content={"error": "INVALID_TRANSFER", "message": str(err)},
        )


@app.post("/api/demo/reset")
def api_demo_reset() -> dict:
    """Reset the SQLite database back to seeded Alice and Bob balances."""
    init_db(get_db_path(), reset=True)
    return {"status": "reset", "accounts": list_accounts()}
