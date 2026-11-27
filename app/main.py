"""FastAPI application and REST API endpoints for GDG Antigravity Bank."""

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictStr

import seed_db
from app import db, fraud, fx, ledger

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="GDG Antigravity Bank")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class TransferRequest(BaseModel):
    """Payload for an atomic peer-to-peer transfer request."""

    from_account_id: str
    to_account_id: str
    amount: StrictStr


@app.get("/", include_in_schema=False)
def read_index() -> FileResponse:
    """Serve the single-page banking dashboard."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health_check() -> dict[str, str]:
    """Return service health check payload."""
    return {"status": "ok"}


@app.get("/api/accounts")
def get_accounts() -> dict[str, list[dict[str, Any]]]:
    """List all accounts with formatted balances and fraud flags."""
    return {"accounts": ledger.list_accounts()}


@app.get("/api/transfers")
def get_transfers() -> dict[str, list[dict[str, Any]]]:
    """List all transfer attempts including fraud-blocked audit rows."""
    return {"transfers": ledger.list_transfers()}


@app.get("/api/ledger")
def get_ledger() -> dict[str, list[dict[str, Any]]]:
    """List all double-entry ledger debit and credit entries."""
    return {"entries": ledger.list_ledger_entries()}


@app.get("/api/fx-rates")
def get_fx_rates() -> dict[str, Any]:
    """Return the configured multi-currency FX rates table."""
    return fx.get_fx_rates_table()


@app.post("/api/transfers", status_code=status.HTTP_201_CREATED)
def create_transfer(
    payload: TransferRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    """Execute an atomic double-entry transfer with idempotency and fraud checks."""
    if idempotency_key is None or not idempotency_key.strip():
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "MISSING_IDEMPOTENCY_KEY",
                "message": "Idempotency-Key header is required.",
            },
        )

    try:
        result = ledger.execute_transfer(
            from_account_id=payload.from_account_id,
            to_account_id=payload.to_account_id,
            amount=payload.amount,
            idempotency_key=idempotency_key.strip(),
        )
    except fraud.FraudLimitExceededError as err:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "error": err.code,
                "message": err.message,
                "usd_equivalent": err.usd_equivalent_str,
                "limit_usd": err.limit_usd_str,
                "amount_cents": err.amount_cents,
                "usd_equivalent_cents": err.usd_equivalent_cents,
                "limit_usd_cents": err.limit_usd_cents,
            },
        )
    except ledger.InsufficientFundsError as err:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "INSUFFICIENT_FUNDS", "message": str(err)},
        )
    except ledger.DuplicateIdempotencyKeyError as err:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": "DUPLICATE_IDEMPOTENCY_KEY",
                "message": str(err),
                "existing_transfer": err.existing_transfer,
            },
        )
    except ledger.AccountNotFoundError as err:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "ACCOUNT_NOT_FOUND", "message": str(err)},
        )
    except ledger.InvalidTransferError as err:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "INVALID_TRANSFER", "message": str(err)},
        )

    return JSONResponse(status_code=status.HTTP_201_CREATED, content=result)


@app.post("/api/demo/reset")
def reset_demo() -> dict[str, Any]:
    """Reset the SQLite database to the seeded demo accounts and state."""
    seed_db.init_db(db.get_db_path(), reset=True)
    return {"status": "reset", "accounts": ledger.list_accounts()}
