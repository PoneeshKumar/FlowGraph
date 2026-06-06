def to_usd_cents(amount_cents: int, currency: str) -> int:
    """Convert amount_cents in the given currency to USD cents.

    Uses 1:1 for USD. Extend with live FX rates for other currencies.
    """
    if currency.upper() == "USD":
        return amount_cents
    # Placeholder — add real exchange rates per currency as needed
    return amount_cents
