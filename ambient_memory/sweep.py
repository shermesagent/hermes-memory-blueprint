#!/usr/bin/env python3
"""
ambient_memory/sweep.py — Cron poller for ended sessions.

Runs every 2–4 hours via ``hermes cron --no-agent``.

Polls ~/.hermes/state.db for sessions whose ``ended_at`` timestamp is
after the last known checkpoint, then runs the capture pipeline on each
session to extract facts into the memory store.

Tracks progress via ``sweep_state.json`` so it never re-processes the
same session twice.  Skips cron-originated sessions (to avoid endless
feedback loops).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

# ---------------------------------------------------------------------------
# Paths — all resolved via Path.home()
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
STATE_DB = Path.home() / ".hermes" / "state.db"
MEMORY_DB = Path.home() / ".hermes" / "memory_store.db"
STATE_FILE = Path.home() / ".hermes" / "ambient_memory" / "sweep_state.json"
LOG_DIR = Path.home() / ".hermes" / "ambient_memory" / "logs"

# Ensure log directory exists
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"sweep_{datetime.now().strftime('%Y%m%d')}.log"
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("sweep")

# Wire up Hermes venv imports
sys.path.insert(0, str(HERMES_VENV))
from plugins.memory.holographic.store import MemoryStore
from plugins.memory.holographic.retrieval import FactRetriever

# Add ambient_memory package to path so we can import capture
sys.path.insert(0, str(Path.home() / ".hermes" / "ambient_memory"))
from capture import capture_from_messages


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Load sweep progress from sweep_state.json.

    Returns:
        Dict with keys 'last_check' (ISO timestamp or None) and
        'processed' (list of session IDs already handled).
    """
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt sweep_state.json — starting fresh.")
    return {"last_check": None, "processed": []}


def save_state(state: dict) -> None:
    """Persist sweep progress to sweep_state.json."""
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------

def get_ended_sessions(state: dict) -> List[dict]:
    """Find sessions that ended since the last checkpoint.

    Skips:
      - Sessions already present in the processed list.
      - Sessions whose ``source`` is 'cron' (to prevent feedback loops).

    Args:
        state: Dict from load_state() with 'last_check' and 'processed'.

    Returns:
        List of session dicts with keys: id, source, title, ended_at,
        message_count.
    """
    conn = sqlite3.connect(str(STATE_DB))
    conn.row_factory = sqlite3.Row

    last_check = state.get("last_check")
    processed = set(state.get("processed", []))

    if last_check:
        rows = conn.execute(
            "SELECT id, source, title, ended_at, message_count "
            "FROM sessions "
            "WHERE ended_at IS NOT NULL AND ended_at > ? "
            "ORDER BY ended_at ASC LIMIT 50",
            (last_check,),
        ).fetchall()
    else:
        # First-ever run: grab the 10 most recently ended sessions
        rows = conn.execute(
            "SELECT id, source, title, ended_at, message_count "
            "FROM sessions "
            "WHERE ended_at IS NOT NULL "
            "ORDER BY ended_at DESC LIMIT 10"
        ).fetchall()

    sessions = []
    for r in rows:
        sid = r["id"]
        if sid in processed:
            continue
        if r["source"] == "cron":
            continue
        sessions.append(dict(r))

    conn.close()
    return sessions


def get_session_messages(session_id: str) -> List[dict]:
    """Fetch user + assistant messages for a given session.

    Args:
        session_id: The session UUID from state.db.

    Returns:
        List of dicts with 'role' and 'content' keys.
    """
    conn = sqlite3.connect(str(STATE_DB))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT role, content FROM messages "
        "WHERE session_id = ? AND role IN ('user', 'assistant') "
        "ORDER BY id ASC",
        (session_id,),
    ).fetchall()

    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in rows if r["content"]]


# ---------------------------------------------------------------------------
# Main sweep logic
# ---------------------------------------------------------------------------

def main():
    """Run a single sweep pass.

    Discovers newly-ended sessions, runs the capture pipeline on each,
    updates sweep_state.json, and prints a JSON summary to stdout for
    cron delivery.
    """
    logger.info("Sweep starting")

    state = load_state()
    store = MemoryStore(db_path=str(MEMORY_DB), default_trust=0.6, hrr_dim=1024)
    retriever = FactRetriever(store=store, temporal_decay_half_life=0, hrr_weight=0.3)

    sessions = get_ended_sessions(state)
    logger.info("Found %d new ended sessions", len(sessions))

    total_added = 0
    total_promoted = 0
    processed_ids = state.get("processed", [])

    for sess in sessions:
        sid = sess["id"]
        logger.info(
            "Processing %s... (%s, %d msgs)",
            sid[:24],
            sess.get("source", "?"),
            sess.get("message_count", 0),
        )

        messages = get_session_messages(sid)
        if not messages:
            logger.debug("No messages for %s...", sid[:24])
            processed_ids.append(sid)
            continue

        # Run full capture pipeline on this session's messages
        results = capture_from_messages(
            messages,
            store=store,
            retriever=retriever,
            max_facts=8,
        )

        for result in results:
            if result["action"] == "added":
                total_added += 1
            elif result["action"] == "promoted":
                total_promoted += 1
                logger.info(
                    "  Promoted fact #%d (was %.2f)",
                    result["fact_id"],
                    result.get("trust_before", 0),
                )

        processed_ids.append(sid)

    # Update state checkpoint
    if sessions:
        newest_end = max(s["ended_at"] for s in sessions)
        state["last_check"] = newest_end
    state["processed"] = processed_ids[-500:]  # keep bounded
    save_state(state)

    store._conn.close()
    logger.info(
        "Sweep complete: %d added, %d promoted, %d sessions",
        total_added,
        total_promoted,
        len(sessions),
    )

    # Print summary JSON for cron delivery
    summary = {
        "sessions_checked": len(sessions),
        "facts_added": total_added,
        "facts_promoted": total_promoted,
        "last_check": state.get("last_check"),
    }
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
