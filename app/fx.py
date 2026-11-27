"""Exact fixed-point currency parsing, formatting, and FX conversion utilities."""

from decimal import Decimal, ROUND_HALF_UP
import re

SUPPORTED_CURRENCIES = ("USD", "EUR", "PHP")
CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "PHP": "₱",
}
USD_PER_UNIT = {
    "USD": Decimal("1.00"),
    "EUR": Decimal("1.08"),
    "PHP": Decimal("0.018"),
}

_AMOUNT_PATTERN = re.compile(r"^\d+(?:\.\d{1,2})?\Z", re.ASCII)
_FX_RATE_QUANT = Decimal("0.000001")
_MINOR_UNIT_QUANT = Decimal("1")
_MINOR_MULTIPLIER = Decimal("100")


def _validate_currency(currency: str) -> str:
    """Validate that a currency code is one of SUPPORTED_CURRENCIES."""
    if not isinstance(currency, str) or currency not in SUPPORTED_CURRENCIES:
        raise ValueError(
            f"Unsupported currency '{currency}'. Expected one of: {', '.join(SUPPORTED_CURRENCIES)}."
        )
    return currency


def _validate_int_minor_units(minor_units: int, field_name: str = "minor_units") -> int:
    """Validate that minor units are provided as an exact integer (not bool or float)."""
    if isinstance(minor_units, bool) or not isinstance(minor_units, int):
        raise ValueError(f"{field_name} must be an integer, got {type(minor_units).__name__}.")
    return minor_units


def get_fx_rates_table() -> dict:
    """Return the static FX rate table anchored to USD using exact Decimal arithmetic."""
    return {
        "base": "USD",
        "usd_per_unit": {
            currency: str(USD_PER_UNIT[currency])
            for currency in SUPPORTED_CURRENCIES
        },
        "pairs": {
            f"{source}/{target}": str(
                (USD_PER_UNIT[source] / USD_PER_UNIT[target]).quantize(
                    _FX_RATE_QUANT, rounding=ROUND_HALF_UP
                )
            )
            for source in SUPPORTED_CURRENCIES
            for target in SUPPORTED_CURRENCIES
            if source != target
        },
    }


def parse_decimal_to_minor_units(amount_str: str) -> int:
    """Parse a positive decimal string with up to 2 fractional digits into integer minor units."""
    if not isinstance(amount_str, str):
        raise ValueError("Amount must be provided as a decimal string.")
    cleaned = amount_str.strip()
    if not _AMOUNT_PATTERN.match(cleaned):
        raise ValueError(
            f"Invalid amount '{amount_str}'. Provide a positive number with up to 2 decimal places."
        )
    minor_units = int(
        (Decimal(cleaned) * _MINOR_MULTIPLIER).quantize(
            _MINOR_UNIT_QUANT, rounding=ROUND_HALF_UP
        )
    )
    if minor_units <= 0:
        raise ValueError("Amount must be greater than zero.")
    return minor_units


def minor_units_to_decimal_str(minor_units: int) -> str:
    """Format integer minor units into an ungrouped fixed-point decimal string (e.g. '5000.00')."""
    units = _validate_int_minor_units(minor_units)
    sign = "-" if units < 0 else ""
    abs_u = abs(units)
    return f"{sign}{abs_u // 100}.{abs_u % 100:02d}"


def format_minor_units(minor_units: int, currency: str = "USD") -> str:
    """Format integer minor units with currency symbol and thousands separators."""
    units = _validate_int_minor_units(minor_units)
    _validate_currency(currency)
    sign = "-" if units < 0 else ""
    abs_u = abs(units)
    symbol = CURRENCY_SYMBOLS[currency]
    return f"{sign}{symbol}{abs_u // 100:,}.{abs_u % 100:02d}"


def convert_minor_units(
    amount_minor: int, source_currency: str, target_currency: str
) -> tuple[int, int, str]:
    """Convert minor units from source_currency to target_currency with USD equivalent and FX rate."""
    units = _validate_int_minor_units(amount_minor, field_name="amount_minor")
    _validate_currency(source_currency)
    _validate_currency(target_currency)

    usd_equivalent_minor = int(
        (Decimal(units) * USD_PER_UNIT[source_currency]).quantize(
            _MINOR_UNIT_QUANT, rounding=ROUND_HALF_UP
        )
    )
    if source_currency == target_currency:
        return (units, usd_equivalent_minor, "1.000000")

    rate = USD_PER_UNIT[source_currency] / USD_PER_UNIT[target_currency]
    target_amount_minor = int(
        (Decimal(units) * rate).quantize(_MINOR_UNIT_QUANT, rounding=ROUND_HALF_UP)
    )
    fx_rate_str = str(rate.quantize(_FX_RATE_QUANT, rounding=ROUND_HALF_UP))
    return (target_amount_minor, usd_equivalent_minor, fx_rate_str)
