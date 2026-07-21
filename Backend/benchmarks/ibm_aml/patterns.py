"""
IBM AML patterns file parser.

Parses HI-Small_Patterns.txt to extract labeled CYCLE laundering groups.

The patterns file format (as published with the IBM AML dataset) uses a multi-line
structure per group. Based on the published dataset and the AMLworld paper, each group
starts with a header block and is followed by transaction rows. The exact delimiter
varies across dataset versions, so we implement two strategies:

Strategy A (primary): parse the structured patterns file looking for "CYCLE" typology
headers, then collect the account pairs in each group.

Strategy B (fallback): if the patterns file is absent or unparseable, derive cycle
groups from the transactions CSV itself by finding strongly-connected components
(every node both sends and receives) in the Is Laundering == 1 subgraph.

Usage:
    from benchmarks.ibm_aml.patterns import load_cycle_groups
    groups = load_cycle_groups("HI-Small_Patterns.txt", "HI-Small_Trans.csv")
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Timestamp formats seen in IBM AML dataset
_TS_FORMATS = ["%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M"]

# The HI-Small CSV has two columns both named "Account" (from and to).
# csv.DictReader silently drops the second, so we must read positionally.
_CSV_COLS = [
    "Timestamp", "From Bank", "Account", "To Bank", "Account.1",
    "Amount Received", "Receiving Currency", "Amount Paid",
    "Payment Currency", "Payment Format", "Is Laundering",
]


def _row_from_parts(parts: list[str]) -> dict:
    """Map a positional CSV row to a dict with unambiguous column names."""
    row = {}
    for i, key in enumerate(_CSV_COLS):
        row[key] = parts[i].strip() if i < len(parts) else ""
    return row


def _parse_ts(raw: str) -> Optional[datetime]:
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _account_key(bank: str, account: str) -> str:
    """Stable hashed account ID that avoids cross-bank collisions."""
    return hashlib.sha256(f"{bank.strip()}:{account.strip()}".encode()).hexdigest()[:32]


@dataclass
class CycleGroup:
    """One labeled CYCLE laundering pattern from the IBM AML dataset."""

    group_id: int
    accounts: list[str]           # hashed account IDs, deduped, in appearance order
    n_hops: int                   # number of edges in the cycle
    min_amount_cents: int
    max_amount_cents: int
    span_seconds: int             # max_ts − min_ts across all edges in the group
    currencies: set[str]          # set of Payment Currency values
    is_cross_currency: bool       # True if > 1 currency (FX mismatch blindspot)
    raw_rows: list[dict] = field(default_factory=list, repr=False)
    typology: str = "CYCLE"       # laundering typology label from the patterns file

    @property
    def account_set(self) -> frozenset[str]:
        return frozenset(self.accounts)


def _build_group_from_rows(
    group_id: int, rows: list[dict], typology: str = "CYCLE"
) -> Optional[CycleGroup]:
    """Build a CycleGroup from a list of CSV row dicts for one pattern block."""
    if not rows:
        return None

    accounts_ordered: list[str] = []
    seen: set[str] = set()
    amounts: list[int] = []
    timestamps: list[datetime] = []
    currencies: set[str] = set()

    for row in rows:
        try:
            from_key = _account_key(row["From Bank"], row["Account"])
            to_key   = _account_key(row["To Bank"],   row["Account.1"])
            amount   = int(float(row["Amount Paid"]) * 100)
            ts       = _parse_ts(row.get("Timestamp", ""))
            currency = row.get("Payment Currency", "UNKNOWN").strip()
        except (KeyError, ValueError) as e:
            logger.debug("Skipping malformed row in group %d: %s", group_id, e)
            continue

        for key in (from_key, to_key):
            if key not in seen:
                accounts_ordered.append(key)
                seen.add(key)

        amounts.append(amount)
        if ts:
            timestamps.append(ts)
        currencies.add(currency)

    if not amounts or not timestamps:
        return None

    amounts_arr = np.asarray(amounts, dtype=np.int64)
    epochs = np.fromiter((t.timestamp() for t in timestamps), dtype=np.float64)
    span = int(np.ptp(epochs))

    return CycleGroup(
        group_id=group_id,
        accounts=accounts_ordered,
        n_hops=len(rows),
        min_amount_cents=int(amounts_arr.min()),
        max_amount_cents=int(amounts_arr.max()),
        span_seconds=span,
        currencies=currencies,
        is_cross_currency=len(currencies) > 1,
        raw_rows=rows,
        typology=typology,
    )


# ---------------------------------------------------------------------------
# Strategy A: parse the structured patterns file
# ---------------------------------------------------------------------------

def _parse_patterns_file(
    patterns_path: Path,
    typologies: frozenset = frozenset({"CYCLE"}),
) -> list[CycleGroup]:
    """
    Parse HI-Small_Patterns.txt.

    Actual format (confirmed from file inspection):
      BEGIN LAUNDERING ATTEMPT - CYCLE:  Max N hops
      <data rows — positional CSV, same column layout as HI-Small_Trans.csv>
      END LAUNDERING ATTEMPT - CYCLE

    Only blocks whose typology is in `typologies` are collected; the rest are
    counted and skipped. The default {"CYCLE"} preserves load_cycle_groups behavior.
    """
    groups: list[CycleGroup] = []
    typology_counts: dict[str, int] = {}

    group_id = 0
    in_target = False
    current_typology = "CYCLE"
    current_rows: list[dict] = []

    _BEGIN_RE = re.compile(r"^BEGIN LAUNDERING ATTEMPT - ([A-Z\-]+)", re.IGNORECASE)
    _END_RE   = re.compile(r"^END LAUNDERING ATTEMPT",                 re.IGNORECASE)

    def _flush():
        nonlocal group_id, current_rows, in_target
        if in_target and current_rows:
            g = _build_group_from_rows(group_id, current_rows, current_typology)
            if g:
                groups.append(g)
                group_id += 1
        current_rows = []
        in_target = False

    with open(patterns_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue

            begin_m = _BEGIN_RE.match(stripped)
            if begin_m:
                _flush()
                typology = begin_m.group(1).upper().rstrip(":")
                typology_counts[typology] = typology_counts.get(typology, 0) + 1
                in_target = typology in typologies
                current_typology = typology
                continue

            if _END_RE.match(stripped):
                _flush()
                continue

            # Data row — parse positionally (no header row in pattern blocks)
            if in_target:
                parts = [p.strip() for p in stripped.split(",")]
                if len(parts) >= 9:  # need at least through Payment Currency
                    current_rows.append(_row_from_parts(parts))

    _flush()

    logger.info(
        "Patterns file parsed: %d groups for %s | all typologies: %s",
        len(groups),
        sorted(typologies),
        typology_counts,
    )
    return groups


def _is_data_row(line: str) -> bool:
    """Heuristic: a data row has many commas and starts with a digit or date-like token."""
    if line.count(",") < 4:
        return False
    first = line.split(",")[0].strip()
    return bool(re.match(r"^\d", first))


# ---------------------------------------------------------------------------
# Strategy B: derive cycle groups from the CSV Is Laundering column
# ---------------------------------------------------------------------------

def _derive_cycle_groups_from_csv(csv_path: Path) -> list[CycleGroup]:
    """
    Fallback when the patterns file is absent or yields no groups.

    Builds a directed graph of Is Laundering == 1 transactions and finds strongly
    connected components (SCCs) with ≥ 2 nodes — each SCC where every node both
    sends and receives is treated as a CYCLE group.

    Uses a simple iterative Tarjan SCC. Works on datasets up to ~100k labeled rows.
    """
    logger.info("Deriving cycle groups from CSV (fallback — patterns file unavailable)")

    edges: list[tuple[str, str, dict]] = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header row
        for parts in reader:
            row = _row_from_parts(parts)
            if row.get("Is Laundering", "0") != "1":
                continue
            from_key = _account_key(row["From Bank"], row["Account"])
            to_key   = _account_key(row["To Bank"],   row["Account.1"])
            edges.append((from_key, to_key, row))

    if not edges:
        logger.warning("No Is Laundering == 1 rows found in CSV")
        return []

    # Build adjacency for SCC
    nodes = {n for e in edges for n in (e[0], e[1])}
    adj:  dict[str, list[str]] = {n: [] for n in nodes}
    radj: dict[str, list[str]] = {n: [] for n in nodes}
    for src, dst, _ in edges:
        adj[src].append(dst)
        radj[dst].append(src)

    # Kosaraju's two-pass SCC
    visited: set[str] = set()
    order: list[str] = []

    def dfs1(v: str) -> None:
        stack = [(v, iter(adj[v]))]
        visited.add(v)
        while stack:
            node, children = stack[-1]
            try:
                child = next(children)
                if child not in visited:
                    visited.add(child)
                    stack.append((child, iter(adj[child])))
            except StopIteration:
                order.append(node)
                stack.pop()

    for node in nodes:
        if node not in visited:
            dfs1(node)

    visited2: set[str] = set()
    components: list[list[str]] = []

    def dfs2(v: str, comp: list[str]) -> None:
        stack = [v]
        visited2.add(v)
        while stack:
            node = stack.pop()
            comp.append(node)
            for child in radj[node]:
                if child not in visited2:
                    visited2.add(child)
                    stack.append(child)

    for node in reversed(order):
        if node not in visited2:
            comp: list[str] = []
            dfs2(node, comp)
            if len(comp) >= 2:
                components.append(comp)

    # Build CycleGroup per SCC
    # Map nodes to their row data
    node_rows: dict[str, list[dict]] = {n: [] for n in nodes}
    for src, _, row in edges:
        node_rows[src].append(row)

    groups: list[CycleGroup] = []
    for gid, comp in enumerate(components):
        comp_set = set(comp)
        rows = [r for n in comp for r in node_rows.get(n, [])
                if _account_key(r["To Bank"], r["Account.1"]) in comp_set]
        g = _build_group_from_rows(gid, rows)
        if g:
            groups.append(g)

    logger.info("Derived %d cycle groups from CSV SCC analysis", len(groups))
    return groups


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_cycle_groups(
    patterns_path: Optional[str | Path],
    csv_path: Optional[str | Path] = None,
) -> list[CycleGroup]:
    """
    Load labeled CYCLE groups, trying Strategy A then falling back to Strategy B.

    Args:
        patterns_path: Path to HI-Small_Patterns.txt (may be None)
        csv_path:      Path to HI-Small_Trans.csv (needed for fallback)

    Returns:
        List of CycleGroup objects
    """
    if patterns_path and Path(patterns_path).exists():
        groups = _parse_patterns_file(Path(patterns_path))
        if groups:
            return groups
        logger.warning(
            "Patterns file parsed but yielded 0 CYCLE groups — falling back to CSV SCC"
        )

    if csv_path and Path(csv_path).exists():
        return _derive_cycle_groups_from_csv(Path(csv_path))

    raise FileNotFoundError(
        "Neither a parseable patterns file nor a CSV path was provided. "
        "Download HI-Small_Patterns.txt and HI-Small_Trans.csv from Kaggle and "
        "place them in benchmarks/data/."
    )


def load_pattern_groups(patterns_path, typologies) -> "list[CycleGroup]":
    """
    Load labeled laundering groups for any set of typologies.

    Unlike load_cycle_groups there is no CSV-derived fallback: non-cycle
    typologies have no structural signature we could derive from the
    transactions file alone, so a missing/unparseable patterns file returns [].

    Args:
        patterns_path: HI-Small_Patterns.txt
        typologies:    e.g. ["GATHER-SCATTER", "BIPARTITE"] (case-insensitive)
    """
    wanted = frozenset(t.strip().upper() for t in typologies)
    path = Path(patterns_path)
    if not path.exists():
        logger.warning("Patterns file %s not found — no groups loaded", path)
        return []
    return _parse_patterns_file(path, wanted)
