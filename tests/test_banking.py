"""Automated pytest verification for double-entry ledger, fraud guardrails, and UI."""

import re
import sqlite3

from fastapi.testclient import TestClient
import pytest

from app.main import app
from seed_db import init_db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Provide a TestClient backed by an isolated temporary SQLite database."""
    db_file = tmp_path / "test_bank.db"
    init_db(db_file, reset=True)
    monkeypatch.setenv("BANK_DB_PATH", str(db_file))
    with TestClient(app) as test_client:
        yield test_client, db_file


def _balances_by_id(client_obj: TestClient) -> dict[str, dict]:
    resp = client_obj.get("/api/accounts")
    assert resp.status_code == 200
    return {acct["id"]: acct for acct in resp.json()["accounts"]}


def test_initial_seeded_balances(client):
    """Accounts start with Alice at $5,000.00 and Bob at $1,200.00."""
    test_client, _ = client
    accounts = _balances_by_id(test_client)

    assert accounts["alice"]["balance_cents"] == 500000
    assert accounts["alice"]["balance_formatted"] == "$5,000.00"
    assert accounts["alice"]["flagged"] is False

    assert accounts["bob"]["balance_cents"] == 120000
    assert accounts["bob"]["balance_formatted"] == "$1,200.00"
    assert accounts["bob"]["flagged"] is False


def test_normal_transfer_updates_balances_and_double_entry_ledger(client):
    """A $250 transfer updates balances atomically and writes balanced DEBIT/CREDIT rows."""
    test_client, db_file = client

    resp = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "tx-normal-250"},
        json={
            "from_account_id": "alice",
            "to_account_id": "bob",
            "amount_cents": 25000,
        },
    )
    assert resp.status_code == 201
    payload = resp.json()
    assert payload["transfer"]["status"] == "COMPLETED"
    assert payload["transfer"]["amount_cents"] == 25000
    assert payload["velocity_flagged"] is False

    accounts = _balances_by_id(test_client)
    assert accounts["alice"]["balance_cents"] == 475000
    assert accounts["bob"]["balance_cents"] == 145000
    assert accounts["alice"]["balance_cents"] + accounts["bob"]["balance_cents"] == 620000

    with sqlite3.connect(db_file) as conn:
        entries = conn.execute(
            """
            SELECT account_id, entry_type, amount_cents, balance_after_cents
            FROM ledger_entries
            ORDER BY id ASC
            """
        ).fetchall()

    assert entries == [
        ("alice", "DEBIT", 25000, 475000),
        ("bob", "CREDIT", 25000, 145000),
    ]


def test_fraud_guardrail_blocks_transfer_over_2000(client):
    """A $3,500 transfer is blocked with HTTP 403 FRAUD_LIMIT_EXCEEDED without moving funds."""
    test_client, db_file = client

    resp = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "tx-fraud-3500"},
        json={
            "from_account_id": "alice",
            "to_account_id": "bob",
            "amount_cents": 350000,
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"] == "FRAUD_LIMIT_EXCEEDED"
    assert body["amount_cents"] == 350000
    assert body["limit_cents"] == 200000

    accounts = _balances_by_id(test_client)
    assert accounts["alice"]["balance_cents"] == 500000
    assert accounts["bob"]["balance_cents"] == 120000

    with sqlite3.connect(db_file) as conn:
        ledger_count = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
        blocked_rows = conn.execute(
            "SELECT status, amount_cents FROM transfers WHERE idempotency_key = ?",
            ("tx-fraud-3500",),
        ).fetchall()

    assert ledger_count == 0
    assert blocked_rows == [("BLOCKED_FRAUD", 350000)]


def test_fraud_limit_exact_boundary(client):
    """Exact $2,000.00 succeeds; $2,000.01 is blocked with HTTP 403."""
    test_client, _ = client

    allowed = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "tx-boundary-200000"},
        json={
            "from_account_id": "alice",
            "to_account_id": "bob",
            "amount_cents": 200000,
        },
    )
    assert allowed.status_code == 201

    blocked = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "tx-boundary-200001"},
        json={
            "from_account_id": "alice",
            "to_account_id": "bob",
            "amount_cents": 200001,
        },
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"] == "FRAUD_LIMIT_EXCEEDED"


def test_idempotency_key_prevents_double_charge(client):
    """Replaying the same Idempotency-Key returns HTTP 409 without charging twice."""
    test_client, _ = client

    first = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "idem-replay-1"},
        json={
            "from_account_id": "alice",
            "to_account_id": "bob",
            "amount_cents": 10000,
        },
    )
    assert first.status_code == 201

    replay = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "idem-replay-1"},
        json={
            "from_account_id": "alice",
            "to_account_id": "bob",
            "amount_cents": 10000,
        },
    )
    assert replay.status_code == 409
    assert replay.json()["error"] == "DUPLICATE_IDEMPOTENCY_KEY"

    accounts = _balances_by_id(test_client)
    assert accounts["alice"]["balance_cents"] == 490000
    assert accounts["bob"]["balance_cents"] == 130000


def test_insufficient_funds_rejected(client):
    """Bob ($1,200) attempting to send $1,500 is rejected with HTTP 400 INSUFFICIENT_FUNDS."""
    test_client, _ = client

    resp = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "tx-overdraft-bob"},
        json={
            "from_account_id": "bob",
            "to_account_id": "alice",
            "amount_cents": 150000,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "INSUFFICIENT_FUNDS"

    accounts = _balances_by_id(test_client)
    assert accounts["alice"]["balance_cents"] == 500000
    assert accounts["bob"]["balance_cents"] == 120000


def test_velocity_flagging_after_more_than_three_transfers(client):
    """First 3 transfers in 60s are unflagged; 4th flags the sender account."""
    test_client, _ = client

    for idx in range(1, 4):
        resp = test_client.post(
            "/api/transfers",
            headers={"Idempotency-Key": f"tx-vel-{idx}"},
            json={
                "from_account_id": "alice",
                "to_account_id": "bob",
                "amount_cents": 1000,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["velocity_flagged"] is False

    accounts_before = _balances_by_id(test_client)
    assert accounts_before["alice"]["flagged"] is False

    fourth = test_client.post(
        "/api/transfers",
        headers={"Idempotency-Key": "tx-vel-4"},
        json={
            "from_account_id": "alice",
            "to_account_id": "bob",
            "amount_cents": 1000,
        },
    )
    assert fourth.status_code == 201
    fourth_body = fourth.json()
    assert fourth_body["velocity_flagged"] is True
    assert fourth_body["transfer"]["velocity_flagged"] is True
    assert fourth_body["velocity_alert"]

    accounts_after = _balances_by_id(test_client)
    assert accounts_after["alice"]["flagged"] is True
    assert accounts_after["alice"]["balance_cents"] == 496000


def test_dashboard_root_serves_offline_html(client):
    """GET / serves the banking dashboard without any external HTTP(S) CDN links."""
    test_client, _ = client

    resp = test_client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "Send Money" in html
    assert not re.search(r'https?://', html)
