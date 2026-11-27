---
name: fraud-guardrails
description: Enforces real-time velocity limits, blocks anomalous banking transfers, and writes automated tests. Use when implementing fraud checks, transfer guardrails, or the pytest verification suite.
---

# Protocol
1. Block any single transfer exceeding `$2,000` (`200000` cents) with HTTP `403` (`FRAUD_LIMIT_EXCEEDED`) before debiting accounts, recording a `BLOCKED_FRAUD` audit entry.
2. Flag accounts making more than `3` completed transfers within a rolling `60`-second window (`accounts.flagged = 1`, `transfers.velocity_flagged = 1`, and return a warning in the API response).
3. Write an automated `pytest` suite verifying normal atomic transfers, double-entry ledger integrity, idempotency, insufficient funds, blocked `>$2,000` transfers, and velocity flagging.
4. Run `.venv/bin/pytest` against an isolated temporary copy/path of the database so `seed.db` remains clean.
