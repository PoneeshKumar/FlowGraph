# currency conversion to canonical USD minor units (cents)
# used at normalization time to populate amount_usd_cents on every event so the
# graph + cache can aggregate cross-rail, cross-currency.

# TODO: real FX — use rate-at-transaction-time from a rate source, not a stub.
# Stubbed at 1:1 for now; USD is exact, everything else is treated as 1:1 until
# a real rate table is wired in.
_STUB_RATES = {"USD": 1.0}


def to_usd_cents(amount_cents: int, currency: str) -> int:
    rate = _STUB_RATES.get(currency.upper(), 1.0)
    return int(round(amount_cents * rate))
