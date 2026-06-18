#!/usr/bin/env python3
"""
ambient_memory/consolidate.py — Weekly trust maintenance.

Intended to run via ``hermes cron --no-agent`` on Sunday at 3 AM.

Three passes over the fact store:

Pass 1 — Decay
    Lower trust by -0.02 on ambient-tagged facts that haven't been
    updated in 30+ days.  Floor at 0.31 (just above retrieval minimum)
    so facts are still findable but deprioritised.

Pass 2 — Promote
    Boost facts with helpful_count > 0 by +0.05 per helpful feedback.
    Ceiling at 0.95.  Only applies to ambient-tagged facts so curated
    facts are not accidentally elevated.

Pass 3 — Supersession
    Find contradictory fact pairs (via retriever.contradict()).  When
    entity overlap ≥ 0.4 and content similarity < 0.3, decay the older
    fact by -0.1 to let the newer one win by default.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HERMES_VENV = (
    Path.home()
    / ".hermes"
    / "hermes-agent"
    / "venv"
    / "lib"
    / "python3.11"
    / "site-packages"
)
MEMORY_DB = Path.home() / ".hermes" / "memory_store.db"
LOG_DIR = Path.home() / ".hermes" / "ambient_memory" / "logs"
PREV_STATE = Path.home() / ".hermes" / "ambient_memory" / "consolidate_state.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"consolidate_{datetime.now().strftime('%Y%m%d')}.log"
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("consolidate")

# Wire up Hermes venv
sys.path.insert(0, str(HERMES_VENV))
from plugins.memory.holographic.store import MemoryStore
from plugins.memory.holographic.retrieval import FactRetriever

# ---------------------------------------------------------------------------
# Configurable constants
# ---------------------------------------------------------------------------

STALENESS_DAYS: int = 30       # Days of inactivity before decay kicks in
DECAY_DELTA: float = -0.02     # Trust reduction per decay pass
FLOOR_TRUST: float = 0.31      # Minimum trust for ambient facts
BOOST_DELTA: float = 0.05      # Trust increase per helpful feedback
CEILING_TRUST: float = 0.95    # Maximum trust for promoted facts
SUPERSESSION_DELTA: float = -0.1  # Trust reduction for older contradictory fact


# ---------------------------------------------------------------------------
# Timestamp handling
# ---------------------------------------------------------------------------

def _parse_timestamp(ts: Optional[str]) -> datetime:
    """Parse a timestamp from the database into a timezone-aware datetime.

    Handles:
      - ISO-8601 with trailing Z             (``2024-12-01T08:00:00Z``)
      - ISO-8601 with offset                 (``2024-12-01T08:00:00+00:00``)
      - SQLite-naive timestamps              (``2024-12-01 08:00:00``)
      - None / empty / unparseable           → now (UTC), as a safe fallback

    Args:
        ts: Raw timestamp string from the database.

    Returns:
        Timezone-aware UTC datetime.
    """
    if not ts:
        return datetime.now(timezone.utc)

    # Normalize Z → +00:00 for fromisoformat compatibility
    ts_norm = ts.replace("Z", "+00:00") if "Z" in ts else ts

    try:
        dt = datetime.fromisoformat(ts_norm)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt


# ---------------------------------------------------------------------------
# Pass 1 — Decay
# ---------------------------------------------------------------------------

def run_decay(store) -> int:
    """Decay trust on stale ambient facts.

    Only affects facts tagged 'ambient' whose updated_at timestamp is
    older than STALENESS_DAYS.  Trust is never pushed below FLOOR_TRUST.

    Args:
        store: An active MemoryStore instance.

    Returns:
        Number of facts decayed.
    """
    now = datetime.now(timezone.utc)

    rows = store._conn.execute(
        "SELECT fact_id, trust_score, updated_at, tags "
        "FROM facts "
        "WHERE trust_score > ?",
        (FLOOR_TRUST,),
    ).fetchall()

    decayed = 0
    for r in rows:
        fid = r["fact_id"]
        trust = r["trust_score"]
        tags = r["tags"] or ""

        # Only decay ambient-tagged facts (leave curated facts alone)
        if "ambient" not in tags:
            continue

        updated = _parse_timestamp(r["updated_at"])
        age_days = (now - updated).total_seconds() / 86400

        if age_days >= STALENESS_DAYS:
            new_trust = max(trust + DECAY_DELTA, FLOOR_TRUST)
            if new_trust != trust:
                store.update_fact(fid, trust_delta=DECAY_DELTA)
                decayed += 1
                logger.debug(
                    "  Decayed #%d: %.2f → %.2f (age=%d d)",
                    fid, trust, new_trust, int(age_days),
                )

    logger.info("Decay pass: %d facts decayed", decayed)
    return decayed


# ---------------------------------------------------------------------------
# Pass 2 — Promotion by helpful feedback
# ---------------------------------------------------------------------------

def run_promotion(store) -> int:
    """Promote ambient facts that have received helpful feedback.

    Uses helpful_count as a proxy for real usefulness.  Each count
    contributes BOOST_DELTA trust, capped at CEILING_TRUST.

    Args:
        store: An active MemoryStore instance.

    Returns:
        Number of facts promoted.
    """
    rows = store._conn.execute(
        "SELECT fact_id, trust_score, helpful_count, tags "
        "FROM facts "
        "WHERE helpful_count > 0 AND trust_score < ?",
        (CEILING_TRUST,),
    ).fetchall()

    promoted = 0
    for r in rows:
        fid = r["fact_id"]
        trust = r["trust_score"]
        helpful = r["helpful_count"]
        tags = r["tags"] or ""

        # Only promote ambient facts so curated facts aren't inflated
        if "ambient" not in tags:
            continue

        new_trust = min(trust + BOOST_DELTA * helpful, CEILING_TRUST)
        delta_needed = round(new_trust - trust, 2)

        if delta_needed > 0:
            store.update_fact(fid, trust_delta=delta_needed)
            promoted += 1
            logger.info(
                "  Promoted #%d: %.2f → %.2f (helpful_count=%d)",
                fid, trust, new_trust, helpful,
            )

    logger.info("Promotion pass: %d facts promoted", promoted)
    return promoted


# ---------------------------------------------------------------------------
# Pass 3 — Supersession (contradiction resolution)
# ---------------------------------------------------------------------------

def run_supersession(store, retriever) -> int:
    """Find contradictory fact pairs and decay the older one.

    Uses retriever.contradict() to discover pairs where entity overlap
    is high (≥ 0.4) but content similarity is low (< 0.3) — a signal
    that the same topic has conflicting information.  The older fact
    gets a trust penalty so newer information wins.

    Only affects ambient-tagged facts; curated facts are never decayed
    by contradiction.

    Args:
        store:     An active MemoryStore instance.
        retriever: An active FactRetriever instance.

    Returns:
        Number of older facts decayed.
    """
    try:
        pairs = retriever.contradict(threshold=0.3, limit=20)
    except Exception:
        logger.warning("contradict() failed (numpy may be unavailable)")
        return 0

    if not pairs:
        logger.info("Supersession pass: no contradictions found")
        return 0

    decayed_older = 0
    for pair in pairs:
        fa = pair["fact_a"]
        fb = pair["fact_b"]
        ta = _parse_timestamp(fa.get("updated_at") or fa.get("created_at"))
        tb = _parse_timestamp(fb.get("updated_at") or fb.get("created_at"))
        entity_overlap = pair.get("entity_overlap", 0)
        content_sim = pair.get("content_similarity", 0)

        # Only act on genuine contradictions
        if entity_overlap >= 0.4 and content_sim < 0.3:
            older = fa if ta < tb else fb
            older_fid = older["fact_id"]
            older_tags = older.get("tags", "") or ""

            if "ambient" not in older_tags:
                continue  # never decay curated facts

            store.update_fact(older_fid, trust_delta=SUPERSESSION_DELTA)
            decayed_older += 1
            logger.info(
                "  Supersession: decayed #%d (entity_overlap=%.2f, sim=%.2f)",
                older_fid, entity_overlap, content_sim,
            )

    logger.info("Supersession pass: %d older facts decayed", decayed_older)
    return decayed_older


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    """Run the full weekly consolidation cycle.

    Connects to the memory store, runs all three passes, logs results,
    writes a state file for downstream consumers, and prints a JSON
    summary to stdout for cron delivery.
    """
    logger.info("=== Consolidation run starting ===")

    store = MemoryStore(db_path=str(MEMORY_DB), default_trust=0.6, hrr_dim=1024)
    retriever = FactRetriever(
        store=store, temporal_decay_half_life=0, hrr_weight=0.3
    )

    decayed = run_decay(store)
    promoted = run_promotion(store)
    superseded = run_supersession(store, retriever)

    store._conn.close()

    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "facts_decayed": decayed,
        "facts_promoted": promoted,
        "facts_superseded": superseded,
    }

    logger.info("=== Consolidation complete: %s ===", json.dumps(summary))

    # Persist state so sweep (or other tools) can inspect last run
    PREV_STATE.write_text(json.dumps(summary, indent=2))

    # Print summary for cron delivery
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
