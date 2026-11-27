---
name: fintech-ledger
description: Builds double-entry banking ledger APIs with idempotency and atomic transactions. Use when implementing account balances, ledger entries, or peer-to-peer transfer endpoints.
---

# Protocol
1. Never use floating-point math for currency; store and calculate all amounts in integer cents (`balance_cents`, `amount_cents`).
2. Wrap every peer-to-peer transfer in an atomic SQLite transaction (`BEGIN IMMEDIATE` / `COMMIT`, `ROLLBACK` on error) that records matching double-entry `DEBIT` and `CREDIT` rows in `ledger_entries`.
3. Reject transfers with HTTP `400` (`INSUFFICIENT_FUNDS`) if `sender.balance_cents < amount_cents`, and reject duplicate `Idempotency-Key` headers with HTTP `409` (`DUPLICATE_IDEMPOTENCY_KEY`) so requests never double-charge.
4. Reuse the pre-seeded `seed.db` schema defined in `seed_db.py` and run Python commands using `.venv/bin/python`.
