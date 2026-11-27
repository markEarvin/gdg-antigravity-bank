"use strict";

const AMOUNT_REGEX = /^\d+(?:\.\d{1,2})?$/;
const FRAUD_LIMIT_USD_CENTS = 200000n;

const CURRENCY_SYMBOLS = {
  USD: "$",
  EUR: "€",
  PHP: "₱",
};

const state = {
  accounts: [],
  accountsById: new Map(),
  previousBalances: new Map(),
  transfers: [],
  ledgerEntries: [],
  fxTable: null,
  initializedSelects: false,
  isSubmitting: false,
};

const dom = {};

document.addEventListener("DOMContentLoaded", () => {
  cacheDom();
  bindEvents();
  refreshDashboardData();
});

function cacheDom() {
  dom.healthPill = document.getElementById("health-pill");
  dom.resetDemoBtn = document.getElementById("reset-demo-btn");
  dom.alertBanner = document.getElementById("alert-banner");
  dom.alertBadge = document.getElementById("alert-badge");
  dom.alertHttpPill = document.getElementById("alert-http-pill");
  dom.alertMessage = document.getElementById("alert-message");
  dom.alertMeta = document.getElementById("alert-meta");
  dom.alertDismissBtn = document.getElementById("alert-dismiss-btn");

  dom.syncStatus = document.getElementById("sync-status");
  dom.accountsGrid = document.getElementById("accounts-grid");

  dom.transferForm = document.getElementById("transfer-form");
  dom.fromAccount = document.getElementById("from-account");
  dom.toAccount = document.getElementById("to-account");
  dom.swapAccountsBtn = document.getElementById("swap-accounts-btn");
  dom.amountInput = document.getElementById("amount-input");
  dom.amountCurrencyBadge = document.getElementById("amount-currency-badge");
  dom.guardrailHint = document.getElementById("guardrail-hint");

  dom.fxPreview = document.getElementById("fx-preview");
  dom.fxRateDisplay = document.getElementById("fx-rate-display");
  dom.fxConvertedDisplay = document.getElementById("fx-converted-display");
  dom.fxUsdEquivDisplay = document.getElementById("fx-usd-equiv-display");

  dom.lastIdemKey = document.getElementById("last-idem-key");
  dom.sendSubmitBtn = document.getElementById("send-submit-btn");

  dom.transfersCount = document.getElementById("transfers-count");
  dom.blockedCount = document.getElementById("blocked-count");
  dom.transfersList = document.getElementById("transfers-list");

  dom.ledgerCount = document.getElementById("ledger-count");
  dom.ledgerTbody = document.getElementById("ledger-tbody");
}

function bindEvents() {
  dom.transferForm.addEventListener("submit", handleTransferSubmit);
  dom.resetDemoBtn.addEventListener("click", handleResetDemo);
  dom.alertDismissBtn.addEventListener("click", hideAlert);

  dom.fromAccount.addEventListener("change", () => {
    updateAmountCurrencyBadge();
    updateFxPreview();
  });

  dom.toAccount.addEventListener("change", () => {
    updateFxPreview();
  });

  dom.swapAccountsBtn.addEventListener("click", () => {
    const prevFrom = dom.fromAccount.value;
    const prevTo = dom.toAccount.value;
    if (prevFrom && prevTo) {
      dom.fromAccount.value = prevTo;
      dom.toAccount.value = prevFrom;
      updateAmountCurrencyBadge();
      updateFxPreview();
    }
  });

  dom.amountInput.addEventListener("input", updateFxPreview);

  document.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => applyPreset(btn.getAttribute("data-preset")));
  });
}

function applyPreset(preset) {
  if (preset === "normal") {
    dom.amountInput.value = "250.00";
  } else if (preset === "fraud") {
    dom.amountInput.value = "3500.00";
  } else if (preset === "fx") {
    dom.amountInput.value = "100.00";
    if (state.accountsById.has("alice")) {
      dom.fromAccount.value = "alice";
    }
    if (state.accountsById.has("clara")) {
      dom.toAccount.value = "clara";
    }
  }
  updateAmountCurrencyBadge();
  updateFxPreview();
  dom.amountInput.focus();
}

/* ==========================================================================
   Pure Integer / BigInt Decimal Helpers (Zero Floating-Point Currency Math)
   ========================================================================== */

function parseDecimalToMinorUnits(raw) {
  const trimmed = String(raw ?? "").trim();
  if (!AMOUNT_REGEX.test(trimmed)) {
    return null;
  }
  const parts = trimmed.split(".");
  const whole = BigInt(parts[0]);
  const frac = BigInt((parts[1] || "").padEnd(2, "0"));
  return whole * 100n + frac;
}

function normalizeDecimalString(raw) {
  const trimmed = String(raw ?? "").trim();
  if (!AMOUNT_REGEX.test(trimmed)) {
    return null;
  }
  const parts = trimmed.split(".");
  const whole = parts[0].replace(/^0+(?=\d)/, "") || "0";
  const frac = (parts[1] || "").padEnd(2, "0");
  return `${whole}.${frac}`;
}

function formatGroupedBigInt(value) {
  const big = typeof value === "bigint" ? value : BigInt(value ?? 0);
  const negative = big < 0n;
  const absStr = (negative ? -big : big).toString();
  const grouped = absStr.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return negative ? `-${grouped}` : grouped;
}

function formatMinorUnits(minorUnits, currency = "USD") {
  const big = typeof minorUnits === "bigint" ? minorUnits : BigInt(minorUnits ?? 0);
  const negative = big < 0n;
  const absVal = negative ? -big : big;
  const whole = formatGroupedBigInt(absVal / 100n);
  const frac = (absVal % 100n).toString().padStart(2, "0");
  const symbol = CURRENCY_SYMBOLS[currency] || `${currency} `;
  return `${negative ? "-" : ""}${symbol}${whole}.${frac}`;
}

function parseDecimalStringToRational(raw) {
  const str = String(raw ?? "").trim();
  if (!str) return null;

  const sciMatch = str.match(/^(\d+)(?:\.(\d+))?[eE]([+-]?\d+)$/);
  if (sciMatch) {
    const digits = sciMatch[1] + (sciMatch[2] || "");
    const fracLen = (sciMatch[2] || "").length;
    const exp = parseInt(sciMatch[3], 10) - fracLen;
    if (exp >= 0) {
      return { num: BigInt(digits) * 10n ** BigInt(exp), den: 1n };
    }
    return { num: BigInt(digits), den: 10n ** BigInt(-exp) };
  }

  if (!/^\d+(?:\.\d+)?$/.test(str)) {
    return null;
  }
  const [whole, frac = ""] = str.split(".");
  const num = BigInt(whole + frac);
  const den = 10n ** BigInt(frac.length);
  if (den <= 0n || num <= 0n) return null;
  return { num, den };
}

function formatRational(num, den, decimals = 4) {
  if (!den || den <= 0n) return "0.0000";
  const factor = 10n ** BigInt(decimals);
  const scaled = (num * factor + den / 2n) / den;
  const whole = (scaled / factor).toString();
  const frac = (scaled % factor).toString().padStart(decimals, "0");
  return `${whole}.${frac}`;
}

function parseRateEntry(entry) {
  if (entry == null) return null;
  if (typeof entry === "string" || typeof entry === "number") {
    const rawStr = String(entry).trim();
    const r = parseDecimalStringToRational(rawStr);
    if (!r) return null;
    return {
      num: r.num,
      den: r.den,
      display: typeof entry === "string" ? rawStr : formatRational(r.num, r.den, 6),
    };
  }
  if (typeof entry === "object") {
    if (entry.numerator != null && entry.denominator != null) {
      const num = BigInt(entry.numerator);
      const den = BigInt(entry.denominator);
      if (num > 0n && den > 0n) {
        const display = String(
          entry.rate_str ?? entry.rate ?? entry.display ?? formatRational(num, den, 6)
        );
        return { num, den, display };
      }
    }
    const candidate = entry.rate_str ?? entry.rate ?? entry.exchange_rate ?? entry.value;
    if (candidate != null) {
      const r = parseDecimalStringToRational(String(candidate));
      if (r) {
        return {
          num: r.num,
          den: r.den,
          display: typeof candidate === "string" ? candidate : formatRational(r.num, r.den, 6),
        };
      }
    }
    if (entry.rate_micros != null) {
      const num = BigInt(entry.rate_micros);
      const den = 1000000n;
      return { num, den, display: formatRational(num, den, 6) };
    }
    if (entry.rate_bps != null) {
      const num = BigInt(entry.rate_bps);
      const den = 10000n;
      return { num, den, display: formatRational(num, den, 4) };
    }
  }
  return null;
}

function resolveFxRate(fxTable, fromCurrency, toCurrency) {
  if (!fromCurrency || !toCurrency) return null;
  if (fromCurrency === toCurrency) {
    return { num: 1n, den: 1n, display: "1.000000" };
  }
  if (!fxTable || typeof fxTable !== "object") return null;

  let displayOverride = null;
  const pairKey = `${fromCurrency}/${toCurrency}`;
  if (fxTable.pairs && typeof fxTable.pairs === "object" && !Array.isArray(fxTable.pairs)) {
    const pairEntry = parseRateEntry(fxTable.pairs[pairKey]);
    if (pairEntry) {
      displayOverride = pairEntry.display;
    }
  }

  const usdPerUnit = fxTable.usd_per_unit || fxTable.to_usd || fxTable.usd_rates;
  if (usdPerUnit && typeof usdPerUnit === "object") {
    const fromUsd = parseRateEntry(usdPerUnit[fromCurrency]);
    const toUsd = parseRateEntry(usdPerUnit[toCurrency]);
    if (fromUsd && toUsd) {
      const num = fromUsd.num * toUsd.den;
      const den = fromUsd.den * toUsd.num;
      return { num, den, display: displayOverride || formatRational(num, den, 6) };
    }
  }

  const containers = [fxTable.pairs, fxTable.rates, fxTable.matrix, fxTable];
  const pairKeys = [
    pairKey,
    `${fromCurrency}_${toCurrency}`,
    `${fromCurrency}->${toCurrency}`,
    `${fromCurrency}-${toCurrency}`,
    `${fromCurrency}:${toCurrency}`,
  ];

  for (const container of containers) {
    if (!container) continue;
    if (Array.isArray(container)) {
      const match = container.find((item) => {
        if (!item || typeof item !== "object") return false;
        const f = item.from_currency || item.from || item.source_currency || item.base;
        const t = item.to_currency || item.to || item.target_currency || item.quote;
        return f === fromCurrency && t === toCurrency;
      });
      const parsed = parseRateEntry(match);
      if (parsed) return parsed;
    } else if (typeof container === "object") {
      if (container[fromCurrency] && typeof container[fromCurrency] === "object") {
        const direct = parseRateEntry(container[fromCurrency][toCurrency]);
        if (direct) return direct;
      }
      for (const key of pairKeys) {
        if (container[key] != null) {
          const parsed = parseRateEntry(container[key]);
          if (parsed) return parsed;
        }
      }
    }
  }

  if (fxTable.rates && typeof fxTable.rates === "object" && !Array.isArray(fxTable.rates)) {
    const fromRate = parseRateEntry(fxTable.rates[fromCurrency]);
    const toRate = parseRateEntry(fxTable.rates[toCurrency]);
    if (fromRate && toRate) {
      const num = toRate.num * fromRate.den;
      const den = toRate.den * fromRate.num;
      return { num, den, display: formatRational(num, den, 6) };
    }
  }

  return null;
}

function convertMinorUnits(amountMinorUnits, rate) {
  if (!rate || rate.den <= 0n) return null;
  return (amountMinorUnits * rate.num + rate.den / 2n) / rate.den;
}

/* ==========================================================================
   API Requests & Refresh
   ========================================================================== */

async function fetchJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.message || `Request failed (${response.status})`);
  }
  return payload;
}

async function refreshDashboardData() {
  try {
    const [healthRes, accountsRes, transfersRes, ledgerRes, fxRes] = await Promise.all([
      fetchJson("/api/health").catch(() => null),
      fetchJson("/api/accounts"),
      fetchJson("/api/transfers"),
      fetchJson("/api/ledger"),
      fetchJson("/api/fx-rates").catch(() => null),
    ]);

    updateHealthPill(Boolean(healthRes && healthRes.status === "ok"));

    if (fxRes) {
      state.fxTable = fxRes;
    }

    const accounts = Array.isArray(accountsRes.accounts) ? accountsRes.accounts : [];
    state.accounts = accounts;
    state.accountsById = new Map(accounts.map((acc) => [acc.id, acc]));

    state.transfers = Array.isArray(transfersRes.transfers) ? transfersRes.transfers : [];
    state.ledgerEntries = Array.isArray(ledgerRes.entries) ? ledgerRes.entries : [];

    renderAccounts(accounts);
    populateAccountSelects(accounts);
    updateAmountCurrencyBadge();
    updateFxPreview();
    renderTransfers(state.transfers);
    renderLedger(state.ledgerEntries);

    const now = new Date();
    dom.syncStatus.textContent = `Synced ${now.toLocaleTimeString()}`;
  } catch (err) {
    updateHealthPill(false);
    dom.syncStatus.textContent = "Sync error";
    showAlert({
      variant: "danger",
      badge: "SYNC_ERROR",
      httpLabel: "Offline",
      message: err.message || "Unable to fetch latest ledger state.",
      meta: [],
    });
  }
}

function updateHealthPill(isHealthy) {
  dom.healthPill.classList.toggle("status-pill--live", isHealthy);
  dom.healthPill.classList.toggle("status-pill--offline", !isHealthy);
}

/* ==========================================================================
   Rendering: Accounts, Selects, FX Preview, Transfers, Ledger
   ========================================================================== */

function renderAccounts(accounts) {
  if (!accounts.length) {
    dom.accountsGrid.innerHTML = '<div class="empty-state">No accounts found.</div>';
    return;
  }

  const cardsHtml = accounts
    .map((acc) => {
      const currency = acc.currency || "USD";
      const minorRaw = acc.balance_cents ?? acc.minor_units ?? 0;
      const minorUnits = BigInt(minorRaw);
      const formatted = acc.balance_formatted || formatMinorUnits(minorUnits, currency);
      const isFlagged = Boolean(acc.flagged);
      const flagReason = acc.flag_reason || "High transfer velocity (>3 in 60s)";

      let flashClass = "";
      if (state.previousBalances.has(acc.id)) {
        const prev = state.previousBalances.get(acc.id);
        if (minorUnits > prev) flashClass = "balance-card--flash-up";
        else if (minorUnits < prev) flashClass = "balance-card--flash-down";
      }
      state.previousBalances.set(acc.id, minorUnits);

      const currencyBadgeClass =
        currency === "USD" || currency === "EUR" || currency === "PHP"
          ? `currency-badge--${currency}`
          : "currency-badge--OTHER";

      return `
        <article class="balance-card ${isFlagged ? "balance-card--flagged" : ""} ${flashClass}" data-account-id="${escapeHtml(acc.id)}">
          <div class="balance-card__top">
            <div class="account-identity">
              <span class="account-name">${escapeHtml(acc.name || acc.id)}</span>
              <span class="account-handle">@${escapeHtml(acc.id)}</span>
            </div>
            <div class="card-badges">
              <span class="currency-badge ${currencyBadgeClass}">${escapeHtml(currency)}</span>
              ${
                isFlagged
                  ? '<span class="velocity-badge">VELOCITY FLAGGED</span>'
                  : '<span class="clear-badge">Clear</span>'
              }
            </div>
          </div>

          <div class="balance-amount">${escapeHtml(formatted)}</div>

          <div class="balance-card__footer">
            <span class="balance-minor-units">${escapeHtml(formatGroupedBigInt(minorUnits))} minor units</span>
            ${isFlagged ? `<span class="flag-reason-note">${escapeHtml(flagReason)}</span>` : ""}
          </div>
        </article>
      `;
    })
    .join("");

  dom.accountsGrid.innerHTML = cardsHtml;
}

function populateAccountSelects(accounts) {
  const prevFrom = dom.fromAccount.value;
  const prevTo = dom.toAccount.value;

  const optionsHtml = accounts
    .map((acc) => {
      const currency = acc.currency || "USD";
      const balance = acc.balance_formatted || formatMinorUnits(acc.balance_cents ?? 0, currency);
      return `<option value="${escapeHtml(acc.id)}">${escapeHtml(acc.name || acc.id)} (${escapeHtml(currency)} &bull; ${escapeHtml(balance)})</option>`;
    })
    .join("");

  dom.fromAccount.innerHTML = optionsHtml;
  dom.toAccount.innerHTML = optionsHtml;

  const hasAccount = (id) => accounts.some((acc) => acc.id === id);

  if (!state.initializedSelects) {
    dom.fromAccount.value = hasAccount("alice") ? "alice" : accounts[0]?.id || "";
    dom.toAccount.value = hasAccount("bob")
      ? "bob"
      : accounts.find((acc) => acc.id !== dom.fromAccount.value)?.id || accounts[0]?.id || "";
    state.initializedSelects = true;
    return;
  }

  dom.fromAccount.value = hasAccount(prevFrom) ? prevFrom : accounts[0]?.id || "";
  dom.toAccount.value = hasAccount(prevTo)
    ? prevTo
    : accounts.find((acc) => acc.id !== dom.fromAccount.value)?.id || accounts[0]?.id || "";
}

function updateAmountCurrencyBadge() {
  const fromAcc = state.accountsById.get(dom.fromAccount.value);
  const currency = fromAcc?.currency || "USD";
  dom.amountCurrencyBadge.textContent = currency;
}

function updateFxPreview() {
  const fromAcc = state.accountsById.get(dom.fromAccount.value);
  const toAcc = state.accountsById.get(dom.toAccount.value);
  const fromCurrency = fromAcc?.currency || "USD";
  const toCurrency = toAcc?.currency || fromCurrency;

  const minorUnits = parseDecimalToMinorUnits(dom.amountInput.value);

  if (fromCurrency === toCurrency) {
    dom.fxPreview.classList.add("hidden");
    updateGuardrailHint(minorUnits, fromCurrency);
    return;
  }

  const fxRate = resolveFxRate(state.fxTable, fromCurrency, toCurrency);
  const usdRate = resolveFxRate(state.fxTable, fromCurrency, "USD");

  dom.fxPreview.classList.remove("hidden");

  if (!fxRate) {
    dom.fxRateDisplay.textContent = `1 ${fromCurrency} → ${toCurrency} (rate unavailable)`;
    dom.fxConvertedDisplay.textContent = "Computed on submit";
    dom.fxUsdEquivDisplay.textContent = "Checked server-side";
    return;
  }

  dom.fxRateDisplay.textContent = `1 ${fromCurrency} = ${fxRate.display} ${toCurrency}`;

  if (minorUnits == null || minorUnits <= 0n) {
    dom.fxConvertedDisplay.textContent = `Enter valid ${fromCurrency} amount`;
    dom.fxUsdEquivDisplay.textContent = "Limit: $2,000.00 USD";
    dom.fxUsdEquivDisplay.classList.remove("fx-preview__value--over-limit");
    return;
  }

  const convertedMinor = convertMinorUnits(minorUnits, fxRate);
  dom.fxConvertedDisplay.textContent =
    convertedMinor != null
      ? `${formatMinorUnits(convertedMinor, toCurrency)} (${formatGroupedBigInt(convertedMinor)} minor units)`
      : "—";

  const usdMinor = usdRate ? convertMinorUnits(minorUnits, usdRate) : null;
  if (usdMinor != null) {
    const overLimit = usdMinor > FRAUD_LIMIT_USD_CENTS;
    dom.fxUsdEquivDisplay.textContent = `${formatMinorUnits(usdMinor, "USD")} / $2,000.00 limit${
      overLimit ? " (EXCEEDS)" : ""
    }`;
    dom.fxUsdEquivDisplay.classList.toggle("fx-preview__value--over-limit", overLimit);
  } else {
    dom.fxUsdEquivDisplay.textContent = "Limit: $2,000.00 USD";
    dom.fxUsdEquivDisplay.classList.remove("fx-preview__value--over-limit");
  }

  updateGuardrailHint(minorUnits, fromCurrency);
}

function updateGuardrailHint(minorUnits, fromCurrency) {
  if (minorUnits == null || minorUnits <= 0n) {
    dom.guardrailHint.textContent = "Max $2,000.00 USD equiv. per transfer";
    return;
  }
  const usdRate = resolveFxRate(state.fxTable, fromCurrency, "USD");
  const usdMinor = usdRate ? convertMinorUnits(minorUnits, usdRate) : null;
  if (usdMinor != null && usdMinor > FRAUD_LIMIT_USD_CENTS) {
    dom.guardrailHint.textContent = `Warning: ${formatMinorUnits(usdMinor, "USD")} exceeds $2,000.00 limit`;
    return;
  }
  dom.guardrailHint.textContent = "Max $2,000.00 USD equiv. per transfer";
}

function renderTransfers(transfers) {
  dom.transfersCount.textContent = `${transfers.length} total`;
  const blockedTotal = transfers.filter((tx) => tx.status === "BLOCKED_FRAUD").length;
  if (blockedTotal > 0) {
    dom.blockedCount.textContent = `${blockedTotal} blocked`;
    dom.blockedCount.classList.remove("hidden");
  } else {
    dom.blockedCount.classList.add("hidden");
  }

  if (!transfers.length) {
    dom.transfersList.innerHTML = '<div class="empty-state">No transfers recorded yet.</div>';
    return;
  }

  const html = transfers
    .map((tx) => {
      const status = tx.status || "COMPLETED";
      const isBlocked = status === "BLOCKED_FRAUD";
      const isRejected = status === "REJECTED";
      const isVelocity = Boolean(tx.velocity_flagged);

      const fromAcc = state.accountsById.get(tx.from_account_id);
      const toAcc = state.accountsById.get(tx.to_account_id);

      const fromName = tx.from_account_name || fromAcc?.name || tx.from_account_id;
      const toName = tx.to_account_name || toAcc?.name || tx.to_account_id;

      const fromCurrency = tx.from_currency || tx.currency || tx.source_currency || fromAcc?.currency || "USD";
      const toCurrency = tx.to_currency || tx.target_currency || toAcc?.currency || fromCurrency;

      const sentMinor = tx.amount_cents ?? tx.from_amount_cents ?? tx.debit_amount_cents ?? 0;
      const sentFormatted = tx.amount_formatted || tx.from_amount_formatted || formatMinorUnits(sentMinor, fromCurrency);

      const recvMinor =
        tx.target_amount_cents ??
        tx.to_amount_cents ??
        tx.credit_amount_cents ??
        tx.converted_amount_cents ??
        null;
      const recvFormatted =
        tx.target_amount_formatted ||
        tx.to_amount_formatted ||
        tx.credit_amount_formatted ||
        tx.converted_amount_formatted ||
        (recvMinor != null ? formatMinorUnits(recvMinor, toCurrency) : null);

      const usdEquivDisplay =
        tx.usd_equivalent_cents != null
          ? formatMinorUnits(tx.usd_equivalent_cents, "USD")
          : tx.usd_equivalent_str || tx.usd_equivalent || "";

      const fxRateVal = tx.fx_rate ?? tx.exchange_rate ?? tx.fx_rate_str ?? tx.applied_fx_rate ?? null;
      const isCrossCurrency = fromCurrency !== toCurrency || (fxRateVal != null && String(fxRateVal) !== "1" && String(fxRateVal) !== "1.000000");

      const statusClass = isBlocked
        ? "tx-status-pill--blocked"
        : isRejected
          ? "tx-status-pill--rejected"
          : "tx-status-pill--completed";

      const itemClasses = ["audit-item"];
      if (isBlocked) itemClasses.push("audit-item--blocked");
      else if (isVelocity) itemClasses.push("audit-item--velocity");

      const amountLine =
        isCrossCurrency && recvFormatted && !isBlocked
          ? `${escapeHtml(sentFormatted)} ${escapeHtml(fromCurrency)} → ${escapeHtml(recvFormatted)} ${escapeHtml(toCurrency)}`
          : `${escapeHtml(sentFormatted)} ${escapeHtml(fromCurrency)}`;

      const fxBadgeHtml = isCrossCurrency
        ? `<span class="fx-tag">FX ${escapeHtml(fromCurrency)}→${escapeHtml(toCurrency)}${
            fxRateVal != null ? ` @ ${escapeHtml(String(fxRateVal))}` : ""
          }</span>`
        : "";

      const velocityBadgeHtml = isVelocity
        ? '<span class="velocity-tag">VELOCITY FLAGGED</span>'
        : "";

      const reasonHtml = tx.reason
        ? `<div class="audit-reason">${escapeHtml(tx.reason)}</div>`
        : "";

      return `
        <article class="${itemClasses.join(" ")}">
          <div class="audit-item__top">
            <div class="audit-item__badges">
              <span class="tx-status-pill ${statusClass}">${escapeHtml(status)}</span>
              ${velocityBadgeHtml}
              ${fxBadgeHtml}
            </div>
            <span class="audit-item__time">${escapeHtml(tx.created_at || "")}</span>
          </div>

          <div class="audit-item__main">
            <span class="audit-route">${escapeHtml(fromName)} (${escapeHtml(fromCurrency)}) &rarr; ${escapeHtml(toName)} (${escapeHtml(toCurrency)})</span>
            <span class="audit-amounts">${amountLine}</span>
          </div>

          ${reasonHtml}

          <div class="audit-item__meta">
            <span>ID: ${escapeHtml(shortId(tx.id))}</span>
            ${tx.idempotency_key ? `<span>Key: ${escapeHtml(shortId(tx.idempotency_key))}</span>` : ""}
            ${usdEquivDisplay ? `<span>USD Equiv: ${escapeHtml(usdEquivDisplay)}</span>` : ""}
          </div>
        </article>
      `;
    })
    .join("");

  dom.transfersList.innerHTML = html;
}

function renderLedger(entries) {
  dom.ledgerCount.textContent = `${entries.length} ${entries.length === 1 ? "entry" : "entries"}`;

  if (!entries.length) {
    dom.ledgerTbody.innerHTML =
      '<tr><td colspan="8" class="empty-cell">No ledger entries recorded yet.</td></tr>';
    return;
  }

  const rowsHtml = entries
    .map((entry) => {
      const account = state.accountsById.get(entry.account_id);
      const currency = entry.currency || account?.currency || "USD";
      const amountMinor = entry.amount_cents ?? entry.minor_units ?? 0;
      const balanceMinor = entry.balance_after_cents ?? 0;
      const amountFormatted = entry.amount_formatted || formatMinorUnits(amountMinor, currency);
      const balanceFormatted =
        entry.balance_after_formatted || formatMinorUnits(balanceMinor, currency);
      const entryType = entry.entry_type || "DEBIT";
      const currencyBadgeClass =
        currency === "USD" || currency === "EUR" || currency === "PHP"
          ? `currency-badge--${currency}`
          : "currency-badge--OTHER";

      return `
        <tr>
          <td class="mono-cell">#${escapeHtml(String(entry.id ?? ""))}</td>
          <td class="mono-cell">${escapeHtml(entry.created_at || "")}</td>
          <td class="mono-cell" title="${escapeHtml(entry.transfer_id || "")}">${escapeHtml(shortId(entry.transfer_id))}</td>
          <td>${escapeHtml(entry.account_name || account?.name || entry.account_id)}</td>
          <td><span class="entry-type-pill entry-type-pill--${escapeHtml(entryType)}">${escapeHtml(entryType)}</span></td>
          <td><span class="currency-badge ${currencyBadgeClass}">${escapeHtml(currency)}</span></td>
          <td class="num-col">${escapeHtml(amountFormatted)}</td>
          <td class="num-col">${escapeHtml(balanceFormatted)}</td>
        </tr>
      `;
    })
    .join("");

  dom.ledgerTbody.innerHTML = rowsHtml;
}

/* ==========================================================================
   Form Submission & Reset Handlers
   ========================================================================== */

async function handleTransferSubmit(event) {
  event.preventDefault();
  if (state.isSubmitting) return;

  const fromAccountId = dom.fromAccount.value;
  const toAccountId = dom.toAccount.value;
  const rawAmount = dom.amountInput.value;

  const normalizedAmount = normalizeDecimalString(rawAmount);
  const minorUnits = parseDecimalToMinorUnits(rawAmount);

  if (!normalizedAmount || minorUnits == null) {
    showAlert({
      variant: "danger",
      badge: "INVALID_AMOUNT",
      httpLabel: "Client Validation",
      message: "Enter a fixed-point decimal amount with up to 2 decimal places (e.g., 250.00).",
      meta: [],
    });
    return;
  }

  if (minorUnits <= 0n) {
    showAlert({
      variant: "danger",
      badge: "INVALID_TRANSFER",
      httpLabel: "Client Validation",
      message: "Transfer amount must be greater than 0.00.",
      meta: [],
    });
    return;
  }

  if (!fromAccountId || !toAccountId) {
    showAlert({
      variant: "danger",
      badge: "INVALID_TRANSFER",
      httpLabel: "Client Validation",
      message: "Select both sender and recipient accounts.",
      meta: [],
    });
    return;
  }

  const idempotencyKey = generateIdempotencyKey();
  dom.lastIdemKey.textContent = idempotencyKey;

  state.isSubmitting = true;
  dom.sendSubmitBtn.disabled = true;
  dom.sendSubmitBtn.textContent = "Sending Atomic Transfer...";

  try {
    const response = await fetch("/api/transfers", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({
        from_account_id: fromAccountId,
        to_account_id: toAccountId,
        amount: normalizedAmount,
      }),
    });

    const data = await response.json().catch(() => ({}));
    await refreshDashboardData();
    presentTransferOutcome(response.status, data, idempotencyKey, fromAccountId, toAccountId, normalizedAmount);
  } catch (err) {
    showAlert({
      variant: "danger",
      badge: "NETWORK_ERROR",
      httpLabel: "Request Failed",
      message: err.message || "Could not reach /api/transfers.",
      meta: [`Idempotency-Key: ${idempotencyKey}`],
    });
  } finally {
    state.isSubmitting = false;
    dom.sendSubmitBtn.disabled = false;
    dom.sendSubmitBtn.textContent = "Send Money";
  }
}

function presentTransferOutcome(statusCode, data, idempotencyKey, fromAccountId, toAccountId, amountStr) {
  if (statusCode >= 200 && statusCode < 300) {
    const tx = data.transfer && typeof data.transfer === "object" ? data.transfer : data;
    const velocityFlagged = Boolean(
      data.velocity_flagged ||
        tx.velocity_flagged ||
        data.velocity_alert ||
        data.warning ||
        data.velocity_warning
    );

    const fromAcc = state.accountsById.get(tx.from_account_id || fromAccountId);
    const toAcc = state.accountsById.get(tx.to_account_id || toAccountId);
    const fromName = tx.from_account_name || fromAcc?.name || fromAccountId;
    const toName = tx.to_account_name || toAcc?.name || toAccountId;
    const fromCurr = tx.source_currency || tx.from_currency || tx.currency || fromAcc?.currency || "USD";
    const toCurr = tx.target_currency || tx.to_currency || toAcc?.currency || fromCurr;

    const sentDisplay =
      tx.amount_formatted ||
      tx.from_amount_formatted ||
      formatMinorUnits(parseDecimalToMinorUnits(amountStr) ?? 0n, fromCurr);

    const recvMinor =
      tx.target_amount_cents ??
      tx.to_amount_cents ??
      tx.credit_amount_cents ??
      tx.converted_amount_cents;
    const recvDisplay =
      tx.target_amount_formatted ||
      tx.to_amount_formatted ||
      tx.credit_amount_formatted ||
      tx.converted_amount_formatted ||
      (recvMinor != null ? formatMinorUnits(recvMinor, toCurr) : null);

    const fxRateVal = tx.fx_rate ?? tx.exchange_rate ?? tx.fx_rate_str ?? tx.applied_fx_rate;
    const transferId = tx.id || data.transfer_id || "";

    const meta = [];
    if (transferId) meta.push(`Transfer ID: ${transferId}`);
    if (fxRateVal != null && fromCurr !== toCurr) meta.push(`FX Rate: ${fxRateVal}`);
    meta.push(`Idempotency-Key: ${idempotencyKey}`);

    const summaryText =
      fromCurr !== toCurr && recvDisplay
        ? `Transferred ${sentDisplay} (${fromCurr}) → ${recvDisplay} (${toCurr}) from ${fromName} to ${toName}.`
        : `Transferred ${sentDisplay} (${fromCurr}) from ${fromName} to ${toName}.`;

    if (velocityFlagged) {
      const alertText = data.velocity_alert || data.warning || data.velocity_warning;
      const warningMsg = alertText
        ? `${summaryText} (${alertText})`
        : `${summaryText} Velocity guardrail triggered (>3 transfers within 60s).`;
      showAlert({
        variant: "warning",
        badge: "VELOCITY FLAGGED",
        httpLabel: `HTTP ${statusCode}`,
        message: warningMsg,
        meta,
      });
      return;
    }

    showAlert({
      variant: "success",
      badge: "ATOMIC TRANSFER COMPLETED",
      httpLabel: `HTTP ${statusCode}`,
      message: summaryText,
      meta,
    });
    return;
  }

  if (statusCode === 403 || data.error === "FRAUD_LIMIT_EXCEEDED") {
    const meta = [];
    const usdEquivDisplay =
      data.usd_equivalent_cents != null
        ? formatMinorUnits(data.usd_equivalent_cents, "USD")
        : data.usd_equivalent
          ? `$${data.usd_equivalent}`
          : "";
    const limitUsdDisplay =
      data.limit_usd_cents != null
        ? formatMinorUnits(data.limit_usd_cents, "USD")
        : data.limit_usd
          ? `$${data.limit_usd}`
          : "";
    if (usdEquivDisplay) meta.push(`USD Equivalent: ${usdEquivDisplay}`);
    if (limitUsdDisplay) meta.push(`Fraud Limit: ${limitUsdDisplay}`);
    meta.push(`Idempotency-Key: ${idempotencyKey}`);

    showAlert({
      variant: "danger",
      badge: data.error || "FRAUD_LIMIT_EXCEEDED",
      httpLabel: `HTTP ${statusCode}`,
      message: data.message || "Transfer blocked by fraud guardrail ($2,000.00 USD limit).",
      meta,
    });
    return;
  }

  const meta = [`Idempotency-Key: ${idempotencyKey}`];
  if (data.existing_transfer?.id) {
    meta.unshift(`Existing Transfer: ${data.existing_transfer.id}`);
  }

  showAlert({
    variant: "danger",
    badge: data.error || "TRANSFER_ERROR",
    httpLabel: `HTTP ${statusCode}`,
    message: extractErrorMessage(data) || "Transfer request rejected.",
    meta,
  });
}

async function handleResetDemo() {
  dom.resetDemoBtn.disabled = true;
  dom.resetDemoBtn.textContent = "Resetting...";

  try {
    const response = await fetch("/api/demo/reset", { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.message || `Reset failed (${response.status})`);
    }

    state.previousBalances.clear();
    state.initializedSelects = false;
    dom.amountInput.value = "250.00";
    dom.lastIdemKey.textContent = "generated on submit";

    await refreshDashboardData();

    showAlert({
      variant: "success",
      badge: "DEMO RESET",
      httpLabel: `HTTP ${response.status}`,
      message: "Demo database reset to initial seeded multi-currency accounts.",
      meta: ["Alice (USD)", "Bob (USD)", "Clara (EUR)"],
    });
  } catch (err) {
    showAlert({
      variant: "danger",
      badge: "RESET_ERROR",
      httpLabel: "Error",
      message: err.message || "Failed to reset demo database.",
      meta: [],
    });
  } finally {
    dom.resetDemoBtn.disabled = false;
    dom.resetDemoBtn.textContent = "Reset Demo";
  }
}

/* ==========================================================================
   Alert Banner & Utilities
   ========================================================================== */

function showAlert({ variant, badge, httpLabel, message, meta }) {
  dom.alertBanner.classList.remove(
    "hidden",
    "alert-banner--danger",
    "alert-banner--warning",
    "alert-banner--success"
  );
  dom.alertBanner.classList.add(`alert-banner--${variant}`);

  dom.alertBadge.textContent = badge || "NOTICE";
  if (httpLabel) {
    dom.alertHttpPill.textContent = httpLabel;
    dom.alertHttpPill.classList.remove("hidden");
  } else {
    dom.alertHttpPill.classList.add("hidden");
  }

  dom.alertMessage.textContent = message || "";

  if (Array.isArray(meta) && meta.length) {
    dom.alertMeta.innerHTML = meta
      .map((item) => `<span class="alert-meta-chip">${escapeHtml(String(item))}</span>`)
      .join("");
    dom.alertMeta.classList.remove("hidden");
  } else {
    dom.alertMeta.innerHTML = "";
    dom.alertMeta.classList.add("hidden");
  }
}

function hideAlert() {
  dom.alertBanner.classList.add("hidden");
}

function extractErrorMessage(data) {
  if (!data || typeof data !== "object") return "";
  if (typeof data.message === "string" && data.message) return data.message;
  if (typeof data.detail === "string" && data.detail) return data.detail;
  if (Array.isArray(data.detail) && data.detail.length) {
    return data.detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  }
  return "";
}

function generateIdempotencyKey() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  if (window.crypto && typeof window.crypto.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  return `idem-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function shortId(value) {
  const str = String(value ?? "");
  if (str.length <= 16) return str;
  return `${str.slice(0, 8)}...${str.slice(-6)}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
