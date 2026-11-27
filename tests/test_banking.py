"""Automated verification suite for multi-currency ledger, fraud guardrails, and UI."""

from pathlib import Path
import re
import sqlite3

from fastapi.testclient import TestClient
import pytest

from seed_db import init_db


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create an isolated temporary SQLite DB and yield (TestClient, db_file)."""
    db_file = tmp_path / "test_bank.db"
    init_db(db_file, reset=True)
    monkeypatch.setenv("BANK_DB_PATH", str(db_file))

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client, db_file


def _accounts_by_id(test_client: TestClient) -> dict[str, dict]:
    """Fetch GET /api/accounts and index account objects by id."""
    resp = test_client.get("/api/accounts")
    assert resp.status_code == 200
    payload = resp.json()
    assert "accounts" in payload
    return {acct["id"]: acct for acct in payload["accounts"]}


def _query_db(db_file: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Execute a read-only SQLite query against the isolated test DB."""
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()


def _velocity_flagged_from_response(payload: dict) -> bool:
    """Extract boolean velocity_flagged from a POST /api/transfers response payload."""
    if "velocity_flagged" in payload:
        value = payload["velocity_flagged"]
    elif (
        isinstance(payload.get("transfer"), dict)
        and "velocity_flagged" in payload["transfer"]
    ):
        value = payload["transfer"]["velocity_flagged"]
    else:
        raise AssertionError(f"Missing 'velocity_flagged' in transfer response: {payload}")
    assert isinstance(value, bool), f"Expected bool velocity_flagged, got {value!r}"
    return value


def test_initial_multi_currency_seeded_balances(client: tuple[TestClient, Path]) -> None:
    """Verify seeded Alice (USD), Bob (USD), and Clara (EUR) balances and clean flags."""
    test_client, _ = client
    accounts = _accounts_by_id(test_client)

    expected = {
        "alice": ("USD", 500000, "5000.00"),
        "bob": ("USD", 120000, "1200.00"),
        "clara": ("EUR", 250000, "2500.00"),
    }
    for account_id, (currency, balance_cents, balance_str) in expected.items():
        assert account_id in accounts
        acct = accounts[account_id]
        assert acct["currency"] == currency
        assert acct["balance_cents"] == balance_cents
        assert acct["balance"] == balance_str
        assert isinstance(acct["balance_formatted"], str) and acct["balance_formatted"]
        assert acct["flagged"] is False
        assert "flag_reason" in acct and acct["flag_reason"] is None


def test_normal_usd_transfer_and_double_entry_ledger(
    client: tuple[TestClient, Path],
) -> None:
    """Verify atomic $250.00 USD->USD transfer updates balances and balanced ledger rows."""
    test_client, db_file = client
    resp = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "normal-usd-250"},
        json={
            "from_account_id": "alice",
            "to_account_id": "bob",
            "amount": "250.00",
        },
    )
    assert resp.status_code == 201
    assert _velocity_flagged_from_response(resp.json()) is False

    accounts = _accounts_by_id(test_client)
    assert accounts["alice"]["balance_cents"] == 475000
    assert accounts["alice"]["balance"] == "4750.00"
    assert accounts["alice"]["flagged"] is False
    assert accounts["bob"]["balance_cents"] == 145000
    assert accounts["bob"]["balance"] == "1450.00"
    assert accounts["bob"]["flagged"] is False

    transfers = _query_db(db_file, "SELECT * FROM transfers")
    assert len(transfers) == 1
    assert transfers[0]["status"] == "COMPLETED"
    assert transfers[0]["from_account_id"] == "alice"
    assert transfers[0]["to_account_id"] == "bob"
    assert transfers[0]["velocity_flagged"] == 0

    entries = _query_db(db_file, "SELECT * FROM ledger_entries ORDER BY id")
    assert len(entries) == 2
    by_type = {row["entry_type"]: row for row in entries}
    assert set(by_type.keys()) == {"DEBIT", "CREDIT"}

    debit = by_type["DEBIT"]
    assert debit["transfer_id"] == transfers[0]["id"]
    assert debit["account_id"] == "alice"
    assert debit["currency"] == "USD"
    assert debit["amount_cents"] == 25000
    assert debit["balance_after_cents"] == 475000

    credit = by_type["CREDIT"]
    assert credit["transfer_id"] == transfers[0]["id"]
    assert credit["account_id"] == "bob"
    assert credit["currency"] == "USD"
    assert credit["amount_cents"] == 25000
    assert credit["balance_after_cents"] == 145000


def test_cross_currency_usd_to_eur_transfer(client: tuple[TestClient, Path]) -> None:
    """Verify deterministic USD->EUR conversion ($100.00 -> 92.59 EUR) and ledger entries."""
    test_client, db_file = client
    resp = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "fx-usd-to-eur-100"},
        json={
            "from_account_id": "alice",
            "to_account_id": "clara",
            "amount": "100.00",
        },
    )
    assert resp.status_code == 201

    accounts = _accounts_by_id(test_client)
    assert accounts["alice"]["balance_cents"] == 490000
    assert accounts["alice"]["balance"] == "4900.00"
    assert accounts["clara"]["balance_cents"] == 259259
    assert accounts["clara"]["balance"] == "2592.59"

    transfers = _query_db(db_file, "SELECT * FROM transfers")
    assert len(transfers) == 1
    assert transfers[0]["status"] == "COMPLETED"

    entries = _query_db(db_file, "SELECT * FROM ledger_entries ORDER BY id")
    assert len(entries) == 2
    by_type = {row["entry_type"]: row for row in entries}
    assert set(by_type.keys()) == {"DEBIT", "CREDIT"}

    debit = by_type["DEBIT"]
    assert debit["transfer_id"] == transfers[0]["id"]
    assert debit["account_id"] == "alice"
    assert debit["currency"] == "USD"
    assert debit["amount_cents"] == 10000
    assert debit["balance_after_cents"] == 490000

    credit = by_type["CREDIT"]
    assert credit["transfer_id"] == transfers[0]["id"]
    assert credit["account_id"] == "clara"
    assert credit["currency"] == "EUR"
    assert credit["amount_cents"] == 9259
    assert credit["balance_after_cents"] == 259259


def test_fraud_limit_blocks_usd_transfer_over_2000(
    client: tuple[TestClient, Path],
) -> None:
    """Verify $3,500.00 USD transfer is blocked with 403 and writes BLOCKED_FRAUD audit."""
    test_client, db_file = client
    resp = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "fraud-usd-3500"},
        json={
            "from_account_id": "alice",
            "to_account_id": "bob",
            "amount": "3500.00",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "FRAUD_LIMIT_EXCEEDED"

    accounts = _accounts_by_id(test_client)
    assert accounts["alice"]["balance_cents"] == 500000
    assert accounts["bob"]["balance_cents"] == 120000

    transfers = _query_db(db_file, "SELECT * FROM transfers")
    assert len(transfers) == 1
    assert transfers[0]["status"] == "BLOCKED_FRAUD"
    assert transfers[0]["from_account_id"] == "alice"
    assert transfers[0]["to_account_id"] == "bob"

    entries = _query_db(db_file, "SELECT * FROM ledger_entries")
    assert len(entries) == 0


def test_cross_currency_eur_transfer_exceeding_usd_limit_blocked(
    client: tuple[TestClient, Path],
) -> None:
    """Verify 2,000.00 EUR ($2,160.00 USD equiv.) is blocked with 403 FRAUD_LIMIT_EXCEEDED."""
    test_client, db_file = client
    resp = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "fraud-eur-2000"},
        json={
            "from_account_id": "clara",
            "to_account_id": "alice",
            "amount": "2000.00",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "FRAUD_LIMIT_EXCEEDED"

    accounts = _accounts_by_id(test_client)
    assert accounts["clara"]["balance_cents"] == 250000
    assert accounts["alice"]["balance_cents"] == 500000

    transfers = _query_db(db_file, "SELECT * FROM transfers")
    assert len(transfers) == 1
    assert transfers[0]["status"] == "BLOCKED_FRAUD"

    entries = _query_db(db_file, "SELECT * FROM ledger_entries")
    assert len(entries) == 0


def test_single_transfer_exact_limit_boundary(client: tuple[TestClient, Path]) -> None:
    """Verify $2,000.00 USD succeeds (201) while $2,000.01 USD is blocked (403)."""
    test_client, _ = client

    resp_allowed = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "boundary-2000-00"},
        json={
            "from_account_id": "alice",
            "to_account_id": "bob",
            "amount": "2000.00",
        },
    )
    assert resp_allowed.status_code == 201

    accounts = _accounts_by_id(test_client)
    assert accounts["alice"]["balance_cents"] == 300000
    assert accounts["bob"]["balance_cents"] == 320000

    resp_blocked = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "boundary-2000-01"},
        json={
            "from_account_id": "alice",
            "to_account_id": "bob",
            "amount": "2000.01",
        },
    )
    assert resp_blocked.status_code == 403
    assert resp_blocked.json()["error"] == "FRAUD_LIMIT_EXCEEDED"

    accounts_after = _accounts_by_id(test_client)
    assert accounts_after["alice"]["balance_cents"] == 300000
    assert accounts_after["bob"]["balance_cents"] == 320000


def test_duplicate_idempotency_key_returns_409_without_double_charge(
    client: tuple[TestClient, Path],
) -> None:
    """Verify replaying an Idempotency-Key returns 409 and never debits twice."""
    test_client, db_file = client
    payload = {
        "from_account_id": "alice",
        "to_account_id": "bob",
        "amount": "50.00",
    }
    headers = {"Idempotency-Key": "idempotency-replay-1"}

    first_resp = test_client.post("/api/transfers", headers=headers, json=payload)
    assert first_resp.status_code == 201

    replay_resp = test_client.post("/api/transfers", headers=headers, json=payload)
    assert replay_resp.status_code == 409
    assert replay_resp.json()["error"] == "DUPLICATE_IDEMPOTENCY_KEY"

    accounts = _accounts_by_id(test_client)
    assert accounts["alice"]["balance_cents"] == 495000
    assert accounts["bob"]["balance_cents"] == 125000

    transfers = _query_db(db_file, "SELECT * FROM transfers")
    assert len(transfers) == 1
    entries = _query_db(db_file, "SELECT * FROM ledger_entries")
    assert len(entries) == 2


def test_insufficient_funds_overdraft_rejected(client: tuple[TestClient, Path]) -> None:
    """Verify Bob sending $1,500.00 with $1,200.00 balance fails with 400 INSUFFICIENT_FUNDS."""
    test_client, db_file = client
    resp = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "overdraft-bob-1500"},
        json={
            "from_account_id": "bob",
            "to_account_id": "alice",
            "amount": "1500.00",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "INSUFFICIENT_FUNDS"

    accounts = _accounts_by_id(test_client)
    assert accounts["bob"]["balance_cents"] == 120000
    assert accounts["alice"]["balance_cents"] == 500000

    entries = _query_db(db_file, "SELECT * FROM ledger_entries")
    assert len(entries) == 0


def test_velocity_flagging_on_fourth_transfer_in_window(
    client: tuple[TestClient, Path],
) -> None:
    """Verify transfers 1-3 stay unflagged while the 4th within 60s flags transfer and account."""
    test_client, db_file = client

    for idx in range(1, 4):
        resp = test_client.post(
            "/api/transfers",
            headers={"Idempotency-Key": f"velocity-transfer-{idx}"},
            json={
                "from_account_id": "alice",
                "to_account_id": "bob",
                "amount": "10.00",
            },
        )
        assert resp.status_code == 201
        assert _velocity_flagged_from_response(resp.json()) is False

        accounts = _accounts_by_id(test_client)
        assert accounts["alice"]["flagged"] is False

    fourth_resp = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "velocity-transfer-4"},
        json={
            "from_account_id": "alice",
            "to_account_id": "bob",
            "amount": "10.00",
        },
    )
    assert fourth_resp.status_code == 201
    assert _velocity_flagged_from_response(fourth_resp.json()) is True

    accounts_after = _accounts_by_id(test_client)
    assert accounts_after["alice"]["flagged"] is True
    assert accounts_after["alice"]["balance_cents"] == 496000
    assert accounts_after["bob"]["balance_cents"] == 124000

    transfer_rows = _query_db(
        db_file, "SELECT velocity_flagged FROM transfers ORDER BY rowid"
    )
    assert [row["velocity_flagged"] for row in transfer_rows] == [0, 0, 0, 1]


def test_dashboard_html_served_offline_without_cdn_urls(
    client: tuple[TestClient, Path],
) -> None:
    """Verify GET / returns 200, renders 'Send Money', and uses zero external CDN URLs."""
    test_client, _ = client
    resp = test_client.get("/")
    assert resp.status_code == 200
    assert "Send Money" in resp.text

    urls = re.findall(r"https?://[^\s\"'<>]+", resp.text, flags=re.IGNORECASE)
    external_cdn_urls = [u for u in urls if not u.startswith("http://www.w3.org/")]
    assert external_cdn_urls == []
