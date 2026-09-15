"""Live-Postgres tests for the app_meta helpers and the risk_flags additions.

Skips unless the dev container is reachable via POSTGRES_DSN (see the module
docstring of tests/test_viz_smoke.py for the exact value).
"""
import asyncio
import pytest


async def _client():
    from db.postgres import PostgresClient
    pg = PostgresClient()
    await pg.initialize()
    return pg


def _run(coro_fn):
    async def wrapper():
        try:
            pg = await _client()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Postgres not reachable: {exc}")
        try:
            return await coro_fn(pg)
        finally:
            await pg.close()
    return asyncio.run(wrapper())


def test_app_meta_roundtrip():
    async def fn(pg):
        await pg.ensure_app_meta_table()
        await pg.set_app_meta("test_key", {"a": 1, "b": [1, 2]})
        got = await pg.get_app_meta("test_key")
        await pg.set_app_meta("test_key", {"a": 2})
        again = await pg.get_app_meta("test_key")
        missing = await pg.get_app_meta("no_such_key")
        async with pg._get_connection() as conn:
            await conn.execute("DELETE FROM app_meta WHERE key = 'test_key'")
        return got, again, missing
    got, again, missing = _run(fn)
    assert got == {"a": 1, "b": [1, 2]}
    assert again == {"a": 2}          # set overwrites
    assert missing is None


def test_dataset_row_is_seeded():
    async def fn(pg):
        await pg.ensure_app_meta_table()
        return await pg.get_app_meta("dataset")
    ds = _run(fn)
    assert ds["source"] == "ibm-hi-small" and ds["end_ts"] == 1663517880


def test_risk_flag_paging_count_and_status():
    async def fn(pg):
        fp = "test:meta:paging"
        await pg.upsert_risk_flag(flag_type="TESTTYPE", fingerprint=fp, account_ids=["t1"],
                                  risk_level="high", risk_score=0.9, explanation="test row")
        total = await pg.count_risk_flags(flag_type="TESTTYPE")
        page0 = await pg.get_risk_flags(flag_type="TESTTYPE", limit=1, offset=0)
        page9 = await pg.get_risk_flags(flag_type="TESTTYPE", limit=1, offset=99)
        by_type = await pg.count_open_flags_by_type()
        ids = await pg.get_account_ids_for_flag_type("TESTTYPE")
        updated = await pg.update_risk_flag_status(page0[0]["id"], "reviewed")
        gone = await pg.update_risk_flag_status(-1, "reviewed")
        async with pg._get_connection() as conn:
            await conn.execute("DELETE FROM risk_flags WHERE fingerprint = $1", fp)
        return total, page0, page9, by_type, ids, updated, gone
    total, page0, page9, by_type, ids, updated, gone = _run(fn)
    assert total == 1 and len(page0) == 1 and page9 == []
    assert by_type.get("TESTTYPE") == 1
    assert ids == ["t1"]
    assert updated["status"] == "reviewed" and gone is None


def test_clear_risk_flags_removes_every_row_and_returns_count():
    async def fn(pg):
        # snapshot + restore so the dev graph's real flags survive this test
        async with pg._get_connection() as conn:
            rows = await conn.fetch("SELECT * FROM risk_flags")
        await pg.upsert_risk_flag(flag_type="TESTTYPE", fingerprint="test:clear", account_ids=["t"],
                                  risk_level="low", risk_score=0.1, explanation="x")
        n = await pg.clear_risk_flags()
        remaining = await pg.count_risk_flags()
        async with pg._get_connection() as conn:
            for r in rows:
                await conn.execute(
                    "INSERT INTO risk_flags (id, flag_type, fingerprint, account_ids, risk_level, risk_score, "
                    "explanation, details, status, first_detected_at, last_detected_at, detection_count, created_at) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) ON CONFLICT (id) DO NOTHING",
                    r["id"], r["flag_type"], r["fingerprint"], r["account_ids"], r["risk_level"], r["risk_score"],
                    r["explanation"], r["details"], r["status"], r["first_detected_at"], r["last_detected_at"],
                    r["detection_count"], r["created_at"])
        return n, remaining, len(rows)
    n, remaining, before = _run(fn)
    assert n == before + 1 and remaining == 0
