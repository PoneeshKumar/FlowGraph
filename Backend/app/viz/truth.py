"""Dataset-labelled (ground-truth) laundering accounts for the visualiser.

Parses the IBM AML ``HI-Small_Patterns.txt`` once — via ``ml.evaluate.load_ground_truth``,
so the account hashing matches exactly what the ingestor wrote into Neo4j — and caches
the account→typology map. This lets the viewer show the *dataset's own* marks next to
the *pipeline's* marks, tab-for-tab. Degrades to an empty set (every ``truth`` flag
false) if the patterns file is absent, so environments without it still run.
"""
import logging
from typing import Dict, Optional, Set

logger = logging.getLogger("viz.truth")

_TRUTH: Optional[Set[str]] = None
_TYPOLOGY: Optional[Dict[str, str]] = None


def _load() -> None:
    global _TRUTH, _TYPOLOGY
    if _TRUTH is not None:
        return
    truth: Set[str] = set()
    typ_of: Dict[str, str] = {}
    try:
        from ml.evaluate import load_ground_truth
        gt = load_ground_truth()
        for typ, accounts in gt.accounts_by_typology.items():
            for acc in accounts:
                truth.add(acc)
                typ_of.setdefault(acc, typ)   # an account can appear in several patterns
        logger.info("ground truth loaded: %d dataset-marked accounts", len(truth))
    except Exception as exc:  # noqa: BLE001 — missing/unparseable file → empty labels
        logger.warning("ground truth unavailable (%s); dataset tab will be empty", exc)
    _TRUTH, _TYPOLOGY = truth, typ_of


def preload() -> int:
    """Load labels eagerly (called at app startup). Returns the count."""
    _load()
    return len(_TRUTH or ())


def truth_set() -> Set[str]:
    _load()
    return _TRUTH  # type: ignore[return-value]


def typology_of(account_id: str) -> Optional[str]:
    _load()
    return (_TYPOLOGY or {}).get(account_id)
