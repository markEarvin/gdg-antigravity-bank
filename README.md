# GDG Antigravity Bank (Starter Workspace)

Starter repository for building a peer-to-peer banking app with double-entry ledger accounting, real-time fraud guardrails, and a live local dashboard.

## Workspace Layout

```text
gdg-antigravity-bank/
├── .agents/
│   └── skills/
│       ├── fintech-ledger/SKILL.md     # Integer cents, idempotency, atomic double-entry ledger
│       ├── fraud-guardrails/SKILL.md   # $2,000 limit, velocity checks, pytest suite
│       └── banking-ui/SKILL.md         # Responsive single-page dashboard on localhost:3000
├── pyproject.toml
├── requirements.txt
├── seed_db.py                          # Creates & resets SQLite schema + seeded accounts
└── seed.db                             # Pre-seeded SQLite database (Alice: $5,000, Bob: $1,200)
```

## Environment Setup

Dependencies are pre-installed in `.venv` (if recreating on another machine, use `uv`):

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

## Database Reset

Reset `seed.db` to the initial seeded balances (`Alice`: `$5,000.00`, `Bob`: `$1,200.00`):

```bash
.venv/bin/python seed_db.py
```

## Running Tests & Server (Once Implemented)

```bash
.venv/bin/pytest -v
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 3000
```
