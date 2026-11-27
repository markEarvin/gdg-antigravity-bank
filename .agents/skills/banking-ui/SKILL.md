---
name: banking-ui
description: Creates a clean, responsive single-page banking dashboard with instant transfer feedback. Use when building the frontend UI, transfer form, fraud alerts, or configuring the localhost:3000 web server.
---

# Protocol
1. Display live balances for Alice and Bob (formatted from integer cents as USD), live transfer/ledger history, and an instant "Send Money" form.
2. Render a high-contrast red alert banner when Fraud Guardrail blocks a transaction (`HTTP 403 FRAUD_LIMIT_EXCEEDED`) and a visible warning badge when an account is velocity-flagged.
3. Send a unique `Idempotency-Key` header on each transfer submission and refresh balances and history immediately after each transfer.
4. Serve the app via FastAPI/Uvicorn on `http://localhost:3000` (`.venv/bin/uvicorn ... --port 3000`) with zero external CDN dependencies so it renders offline in Antigravity's HTML preview.
