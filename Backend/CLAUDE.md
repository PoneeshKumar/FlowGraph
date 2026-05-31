# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FlowGraph Backend is a Python payment event processing pipeline. It ingests card payment events (auth + settlement), normalizes them, stores them across Neo4j/Redis/PostgreSQL, and streams them via Faust (Kafka).

## Architecture

```
consumer/faust_app.py     # Faust Kafka consumer — ingests raw events
normalizer/card_normalizer.py  # Normalizes raw payloads into typed models
models/card_events.py     # Pydantic event models (source of truth for schema)
generator/card_generator.py    # Generates synthetic card events for testing
db/neo4j.py               # Neo4j client (graph relationships)
db/redis.py               # Redis client (auth lookup cache)
db/postgres.py            # PostgreSQL client (persistent storage)
config.py                 # Environment/connection config
main.py                   # Entry point
```

## Domain Model

### Rails
`Rail` enum: `ACH`, `WIRE`, `CARD`, `CRYPTO` — only CARD is implemented so far.

### Event Types & Status lifecycle
- `CardAuthEvent` (tap/swipe) → status auto-set in `__init__`: `PENDING` if approved, `DECLINED` if not
- `CardSettlementEvent` (money moves, 1–3 days later) → defaults to `SETTLED`
- `ORPHANED` = settlement arrived with no matching auth

Auth and settlement are linked by `authorization_code` (6-char uppercase string).

### Key invariants
- **Amounts in cents** (`amount_cents: int`) — never floats. Use `amount_dollars` property for display only.
- **All timestamps must be UTC and timezone-aware** — validators raise on naive datetimes.
- **Currency codes are 3-char uppercase ISO** — auto-uppercased by validator.
- **Authorization codes are 6-char uppercase** — auto-uppercased by validator.
- **Merchant category codes are exactly 4 digits** — validated to be all digits.
- **PANs are SHA-256 hashed** via `hash_pan()` before storage — never store raw card numbers.
- `raw_payload: dict` on `BasePaymentEvent` preserves the original untouched payload.
- `Config.use_enum_values = False` — pass enum instances, not string values.

## Code Conventions

- File naming: `<domain>_<type>.py` (e.g., `card_events.py`, `card_normalizer.py`, `card_generator.py`) — not generic names like `events.py`.
- Pydantic v2 style: use `@field_validator` with `@classmethod`, not the v1 `@validator`.
- `mode="before"` on timestamp validators to handle raw input before type coercion.
- New payment rails (ACH, WIRE, CRYPTO) follow the same pattern: extend `BasePaymentEvent`, add rail-specific fields, set default `rail` and `event_type` via `Field(default=...)`.

## Running the Backend

No run commands are defined yet — `main.py` and most modules are stubs. When adding startup logic, the Faust app is the expected entry point: `faust -A consumer.faust_app worker`.

## Known Issues in Current Code

- `BasePaymentEvent.raw_payload` has a typo: `default_factor=dict` should be `default_factory=dict` — fix before using.
- `enforcu_utc` validator name is misspelled (missing `e`) — harmless but inconsistent.
