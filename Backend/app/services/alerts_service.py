"""Alerts = rows of Postgres risk_flags, shaped for the UI (spec §6.4–6.5)."""
import json
from datetime import datetime
from typing import Any, Dict, Optional


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if isinstance(v, datetime) else (str(v) if v is not None else None)


def shape_alert(row: Dict[str, Any]) -> Dict[str, Any]:
    details = row.get("details") or {}
    if isinstance(details, str):
        details = json.loads(details)
    ids = list(row.get("account_ids") or [])
    return {
        "id": int(row["id"]),
        "flag_type": row["flag_type"],
        "risk_level": row["risk_level"],
        "risk_score": float(row["risk_score"]),
        "explanation": row["explanation"],
        "account_ids": ids,
        "primary_account": ids[0] if ids else None,
        "details": details,
        "status": row["status"],
        "first_detected_at": _iso(row.get("first_detected_at")),
        "last_detected_at": _iso(row.get("last_detected_at")),
        "detection_count": int(row.get("detection_count") or 1),
    }


async def list_alerts(pg: Any, flag_type: Optional[str], min_level: Optional[str],
                      status: Optional[str], limit: int, offset: int) -> Dict[str, Any]:
    rows = await pg.get_risk_flags(flag_type=flag_type, min_level=min_level, status=status,
                                   limit=limit, offset=offset)
    total = await pg.count_risk_flags(flag_type=flag_type, min_level=min_level, status=status)
    return {"total": int(total), "items": [shape_alert(r) for r in rows]}


async def set_status(pg: Any, flag_id: int, status: str) -> Optional[Dict[str, Any]]:
    row = await pg.update_risk_flag_status(flag_id, status)
    return shape_alert(row) if row else None
