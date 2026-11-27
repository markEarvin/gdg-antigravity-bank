(() => {
  const accountsGrid = document.getElementById("accounts-grid");
  const fromSelect = document.getElementById("from-account");
  const toSelect = document.getElementById("to-account");
  const amountInput = document.getElementById("amount-usd");
  const transferForm = document.getElementById("transfer-form");
  const submitBtn = document.getElementById("submit-transfer-btn");
  const resetDemoBtn = document.getElementById("reset-demo-btn");
  const transfersList = document.getElementById("transfers-list");
  const transferCount = document.getElementById("transfer-count");
  const ledgerTbody = document.getElementById("ledger-tbody");
  const ledgerCount = document.getElementById("ledger-count");
  const alertBanner = document.getElementById("alert-banner");
  const alertTitle = document.getElementById("alert-title");
  const alertMessage = document.getElementById("alert-message");
  const alertDismissBtn = document.getElementById("alert-dismiss-btn");
  const idempotencyPreview = document.getElementById("idempotency-preview");

  let previousBalances = new Map();
  let nextIdempotencyKey = generateIdempotencyKey();

  function generateIdempotencyKey() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return `req-${window.crypto.randomUUID()}`;
    }
    return `req-${Date.now()}-${Math.floor(Math.random() * 1_000_000)}`;
  }

  function refreshIdempotencyKey() {
    nextIdempotencyKey = generateIdempotencyKey();
    idempotencyPreview.textContent = nextIdempotencyKey;
  }

  function parseUsdToCents(rawValue) {
    const cleaned = rawValue.trim().replace(/[$,\s]/g, "");
    const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(cleaned);
    if (!match) {
      return null;
    }
    const wholeDollars = Number.parseInt(match[1], 10);
    const fractionalPart = (match[2] || "").padEnd(2, "0");
    const centsPart = Number.parseInt(fractionalPart || "0", 10);
    const totalCents = wholeDollars * 100 + centsPart;
    return totalCents > 0 ? totalCents : null;
  }

  function showAlert(variant, title, message) {
    alertBanner.className = `alert-banner alert-${variant}`;
    alertTitle.textContent = title;
    alertMessage.textContent = message;
  }

  function hideAlert() {
    alertBanner.className = "alert-banner hidden";
    alertTitle.textContent = "";
    alertMessage.textContent = "";
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  function renderAccounts(accounts) {
    const selectedFrom = fromSelect.value || "alice";
    const selectedTo = toSelect.value || "bob";

    accountsGrid.innerHTML = accounts
      .map((acct) => {
        const prev = previousBalances.get(acct.id);
        const changed = prev !== undefined && prev !== acct.balance_cents;
        const classes = ["account-card"];
        if (acct.flagged) classes.push("flagged");
        if (changed) classes.push("balance-flash");

        const badge = acct.flagged
          ? `<span class="velocity-badge">VELOCITY FLAGGED</span>`
          : `<span class="account-id">@${escapeHtml(acct.id)}</span>`;

        const flagDetail = acct.flag_reason
          ? `<div class="account-flag-reason">${escapeHtml(acct.flag_reason)}</div>`
          : "";

        return `
          <article class="${classes.join(" ")}">
            <div class="account-top">
              <span class="account-name">${escapeHtml(acct.name)}</span>
              ${badge}
            </div>
            <div class="account-balance">${escapeHtml(acct.balance_formatted)}</div>
            <div class="account-cents">${acct.balance_cents.toLocaleString()} cents</div>
            ${flagDetail}
          </article>
        `;
      })
      .join("");

    previousBalances = new Map(accounts.map((acct) => [acct.id, acct.balance_cents]));

    const optionsHtml = accounts
      .map(
        (acct) =>
          `<option value="${escapeHtml(acct.id)}">${escapeHtml(acct.name)} (${escapeHtml(acct.balance_formatted)})</option>`
      )
      .join("");

    fromSelect.innerHTML = optionsHtml;
    toSelect.innerHTML = optionsHtml;

    if (accounts.some((a) => a.id === selectedFrom)) {
      fromSelect.value = selectedFrom;
    }
    if (accounts.some((a) => a.id === selectedTo)) {
      toSelect.value = selectedTo;
    }
  }

  function renderTransfers(transfers) {
    transferCount.textContent = String(transfers.length);
    if (transfers.length === 0) {
      transfersList.innerHTML =
        '<div class="feed-empty">No transfers yet. Try sending $250 or testing the $3,500 Fraud Guardrail.</div>';
      return;
    }

    transfersList.innerHTML = transfers
      .map((tx) => {
        const isBlocked = tx.status === "BLOCKED_FRAUD";
        const statusTag = isBlocked
          ? '<span class="status-tag tag-blocked">BLOCKED FRAUD</span>'
          : '<span class="status-tag tag-completed">COMPLETED</span>';
        const velocityTag = tx.velocity_flagged
          ? '<span class="status-tag tag-velocity">VELOCITY FLAGGED</span>'
          : "";
        const reasonLine = tx.reason
          ? `<div class="feed-meta">${escapeHtml(tx.reason)}</div>`
          : "";

        return `
          <article class="feed-item ${isBlocked ? "blocked" : ""}">
            <div class="feed-item-top">
              <span class="feed-route">${escapeHtml(tx.from_account_id)} &rarr; ${escapeHtml(tx.to_account_id)}</span>
              <span class="feed-amount">${escapeHtml(tx.amount_formatted)}</span>
            </div>
            <div class="feed-meta">
              ${statusTag}
              ${velocityTag}
              <span><code>${escapeHtml(tx.id)}</code></span>
              <span>${escapeHtml(tx.created_at)} UTC</span>
            </div>
            ${reasonLine}
          </article>
        `;
      })
      .join("");
  }

  function renderLedger(entries) {
    ledgerCount.textContent = String(entries.length);
    if (entries.length === 0) {
      ledgerTbody.innerHTML =
        '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:24px;">No ledger entries recorded yet.</td></tr>';
      return;
    }

    ledgerTbody.innerHTML = entries
      .map((entry) => {
        const typeClass = entry.entry_type === "DEBIT" ? "entry-debit" : "entry-credit";
        return `
          <tr>
            <td>#${entry.id}</td>
            <td><code>${escapeHtml(entry.transfer_id)}</code></td>
            <td>${escapeHtml(entry.account_id)}</td>
            <td class="${typeClass}">${escapeHtml(entry.entry_type)}</td>
            <td class="num">${escapeHtml(entry.amount_formatted)}</td>
            <td class="num">${escapeHtml(entry.balance_after_formatted)}</td>
            <td>${escapeHtml(entry.created_at)}</td>
          </tr>
        `;
      })
      .join("");
  }

  async function refreshDashboard() {
    const [acctRes, txRes, ledgerRes] = await Promise.all([
      fetch("/api/accounts"),
      fetch("/api/transfers"),
      fetch("/api/ledger"),
    ]);

    const [acctData, txData, ledgerData] = await Promise.all([
      acctRes.json(),
      txRes.json(),
      ledgerRes.json(),
    ]);

    renderAccounts(acctData.accounts || []);
    renderTransfers(txData.transfers || []);
    renderLedger(ledgerData.entries || []);
  }

  transferForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const fromAccountId = fromSelect.value;
    const toAccountId = toSelect.value;
    const amountCents = parseUsdToCents(amountInput.value);

    if (amountCents === null) {
      showAlert(
        "danger",
        "INVALID AMOUNT",
        "Enter a positive USD dollar amount with up to 2 decimal places (for example: 250.00)."
      );
      return;
    }

    if (fromAccountId === toAccountId) {
      showAlert("danger", "INVALID TRANSFER", "Choose two different accounts for a P2P transfer.");
      return;
    }

    submitBtn.disabled = true;
    const requestKey = nextIdempotencyKey;

    try {
      const response = await fetch("/api/transfers", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": requestKey,
        },
        body: JSON.stringify({
          from_account_id: fromAccountId,
          to_account_id: toAccountId,
          amount_cents: amountCents,
        }),
      });

      const body = await response.json();

      if (response.status === 403 && body.error === "FRAUD_LIMIT_EXCEEDED") {
        showAlert("danger", "FRAUD GUARDRAIL BLOCKED TRANSFER (HTTP 403)", body.message);
      } else if (!response.ok) {
        showAlert("danger", body.error || `TRANSFER REJECTED (HTTP ${response.status})`, body.message || "Transfer failed.");
      } else if (body.velocity_flagged) {
        showAlert(
          "warning",
          "TRANSFER COMPLETED — VELOCITY ALERT TRIGGERED",
          `${body.transfer.amount_formatted} sent from ${body.transfer.from_account_id} to ${body.transfer.to_account_id}. ${body.velocity_alert}`
        );
      } else {
        showAlert(
          "success",
          "TRANSFER COMPLETED ATOMICALLY",
          `${body.transfer.amount_formatted} transferred from ${body.transfer.from_account_id} to ${body.transfer.to_account_id} (double-entry ledger updated).`
        );
      }

      refreshIdempotencyKey();
      await refreshDashboard();
    } catch (err) {
      showAlert("danger", "NETWORK ERROR", err.message || "Could not reach banking API.");
    } finally {
      submitBtn.disabled = false;
    }
  });

  document.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      amountInput.value = btn.getAttribute("data-preset");
      amountInput.focus();
    });
  });

  resetDemoBtn.addEventListener("click", async () => {
    resetDemoBtn.disabled = true;
    try {
      const response = await fetch("/api/demo/reset", { method: "POST" });
      if (!response.ok) {
        throw new Error("Failed to reset demo state.");
      }
      previousBalances.clear();
      refreshIdempotencyKey();
      await refreshDashboard();
      showAlert(
        "success",
        "DEMO RESET",
        "Seeded database restored: Alice ($5,000.00) and Bob ($1,200.00)."
      );
    } catch (err) {
      showAlert("danger", "RESET FAILED", err.message);
    } finally {
      resetDemoBtn.disabled = false;
    }
  });

  alertDismissBtn.addEventListener("click", hideAlert);

  refreshIdempotencyKey();
  refreshDashboard().catch((err) => {
    showAlert("danger", "INITIAL LOAD ERROR", err.message);
  });
})();
