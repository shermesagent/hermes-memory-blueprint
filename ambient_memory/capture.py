#!/usr/bin/env python3
"""
ambient_memory/capture.py — Core extraction, dedup, and write module.

Used by both:
  1. on_session_end hook  (in-process, receives store + retriever)
  2. cron sweep           (headless, creates its own connection)

Provides a full pipeline: extract_bookends → extract_candidates → write_candidate.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("ambient_capture")

# ---------------------------------------------------------------------------
# Store API convenience wrappers (headless-safe, import from Hermes venv)
# ---------------------------------------------------------------------------

_STORE_INSTANCE = None


def get_store(db_path: Optional[str] = None) -> "MemoryStore":
    """Get or create a MemoryStore connection to the fact store.

    Caches instance so a cron sweep reuses one connection across sessions.
    Headless callers MUST pass db_path; hook callers can pass a store directly.

    Args:
        db_path: Path to memory_store.db. Defaults to ~/.hermes/memory_store.db.

    Returns:
        A MemoryStore instance connected to the fact database.
    """
    global _STORE_INSTANCE
    if _STORE_INSTANCE is not None:
        return _STORE_INSTANCE

    _ensure_venv_path()
    from plugins.memory.holographic.store import MemoryStore

    if db_path is None:
        db_path = str(Path.home() / ".hermes" / "memory_store.db")

    _STORE_INSTANCE = MemoryStore(db_path=db_path, default_trust=0.6, hrr_dim=1024)
    return _STORE_INSTANCE


def get_retriever(store) -> "FactRetriever":
    """Get a FactRetriever for semantic search and dedup pre-checks.

    Args:
        store: An active MemoryStore instance.

    Returns:
        A FactRetriever linked to the given store.
    """
    _ensure_venv_path()
    from plugins.memory.holographic.retrieval import FactRetriever

    return FactRetriever(store=store, temporal_decay_half_life=0, hrr_weight=0.3)


def _ensure_venv_path():
    """Add Hermes venv site-packages to sys.path if not already present."""
    venv_path = str(
        Path.home()
        / ".hermes"
        / "hermes-agent"
        / "venv"
        / "lib"
        / "python3.11"
        / "site-packages"
    )
    if venv_path not in sys.path:
        sys.path.insert(0, venv_path)


# ---------------------------------------------------------------------------
# Configurable regex patterns for fact extraction
#
# Users can customize these by monkey-patching or subclassing without
# touching the core logic. Keep re.IGNORECASE on every pattern.
# ---------------------------------------------------------------------------

# Milestone markers: completed, finished, launched, deployed, shipped, resolved
_MILESTONE = re.compile(
    r"\b("
    r"completed|finished|launched|deployed|shipped|resolved"
    r"|got\s+it\s+working|finally\s+done|up\s+and\s+running"
    r"|pushed\s+to\s+(?:prod|production|live|main)"
    r")\b",
    re.IGNORECASE,
)

# User preference markers
_PREFERENCE = re.compile(
    r"\bI\s+(?:prefer|like|love|hate|use|want|need|always|never)\s+",
    re.IGNORECASE,
)

# Decision / agreement markers
_DECISION = re.compile(
    r"\b(?:"
    r"we\s+(?:decided|agreed|chose|settled\s+on|went\s+with)"
    r"|the\s+(?:plan|next\s+step|approach|strategy)\s+is"
    r"|let.s\s+(?:start|begin|move\s+to|go\s+with)"
    r"|going\s+forward\s+we.ll"
    r")\b",
    re.IGNORECASE,
)

# Project / technical facts: paths, versions, ports, URLs, config values
_PROJECT_FACT = re.compile(
    r"\b("
    r"/(?:home|etc|opt|usr|var|tmp)/\S+"          # absolute paths
    r"|~\S*"                                        # home-relative paths
    r"|version\s+\d+\.\d+(?:\.\d+)?"                # version numbers
    r"|port\s+\d{2,5}"                               # port numbers
    r"|https?://\S+"                                 # URLs
    r"|API\s+key|token|endpoint"                     # config tokens
    r"|\.env|\.ya?ml|\.json|\.toml"                  # config file patterns
    r")\b",
    re.IGNORECASE,
)

# Domain markers: cheap regex classification for tagging
_DOMAIN_MARKERS: Dict[str, re.Pattern] = {
    "project": re.compile(
        r"\b(project|deploy|build|release|version|repo|git|ci/cd|pipeline)\b",
        re.IGNORECASE,
    ),
    "pref": re.compile(
        r"\bI\s+(?:prefer|want|like|need|hate|always|never)\b",
        re.IGNORECASE,
    ),
    "tech": re.compile(
        r"\b(python|javascript|typescript|rust|go|docker|kubernetes"
        r"|api|database|sql|nosql|server|linux|macos|windows"
        r"|npm|pip|cargo|brew)\b",
        re.IGNORECASE,
    ),
    "data": re.compile(
        r"\b(data|dataset|csv|json|analytics|metric|log|report|dashboard)\b",
        re.IGNORECASE,
    ),
    "config": re.compile(
        r"\b(config|settings|\.env|environment|variable|secret|credential)\b",
        re.IGNORECASE,
    ),
    "workflow": re.compile(
        r"\b(workflow|automation|cron|schedule|hook|trigger|pipeline)\b",
        re.IGNORECASE,
    ),
}

# Expansion map: augment detected domain tags with related terms
# This acts as a cheap semantic stopgap — capturing a "project" fact
# automatically adds "deploy" and "workflow" tags so retrieval has
# more surface area.
_EXPANSION_MAP: Dict[str, List[str]] = {
    "project":  ["project", "deploy", "workflow", "build"],
    "tech":     ["tech", "python", "tooling", "development"],
    "data":     ["data", "analytics", "report", "metric"],
    "config":   ["config", "settings", "environment"],
    "workflow": ["workflow", "automation", "cron", "pipeline"],
    "pref":     ["pref", "preference", "user-setting"],
}


# ---------------------------------------------------------------------------
# Session text extraction
# ---------------------------------------------------------------------------

def extract_bookends(messages: List[dict], max_first: int = 3, max_last: int = 3) -> str:
    """Extract the high-signal portion of a conversation.

    Returns concatenated text of the first N and last N user+assistant
    messages.  The opening messages carry the user's goal; the closing
    messages carry the resolution.  Together they capture the most
    fact-dense parts of a conversation without re-processing the whole
    session.

    Args:
        messages:  List of dicts with 'role' and 'content' keys.
        max_first: Number of messages to take from the beginning.
        max_last:  Number of messages to take from the end.

    Returns:
        Concatenated bookend text, with '---' separating head from tail.
    """
    user_asst = [m for m in messages if m.get("role") in ("user", "assistant")]
    if not user_asst:
        return ""

    first = user_asst[:max_first]
    last = (
        user_asst[-max_last:]
        if len(user_asst) > max_first + max_last
        else []
    )

    combined = [m["content"] for m in first]
    if last:
        combined.append("---")
        combined.extend(m["content"] for m in last)

    return "\n".join(c for c in combined if c and isinstance(c, str))


# ---------------------------------------------------------------------------
# Candidate extraction
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    """A fact candidate extracted from conversation text.

    Attributes:
        text:       The candidate sentence (truncated to 600 chars max).
        category:   Classification label (e.g. 'milestone', 'preference', 'project').
        tags:       List of domain tags including 'ambient'.
        confidence: Extraction confidence score (0.0–1.0).
    """
    text: str
    category: str
    tags: List[str]
    confidence: float


def extract_candidates(text: str, max_facts: int = 10) -> List[Candidate]:
    """Extract fact-worthy candidates from conversation text.

    Scans sentence-by-sentence for actionable patterns (milestones,
    preferences, decisions, project facts) and wraps each matching
    sentence as a Candidate with a confidence score.

    Strategy (ordered by signal strength):
      1. Milestone markers  → confidence 0.70, category 'milestone'
      2. Project facts      → confidence 0.65, category 'project'
      3. Decision markers   → confidence 0.55, category 'decision'
      4. Preference markers → confidence 0.50, category 'preference'
      5. Multi-domain long  → confidence 0.35, category 'general'

    Args:
        text:      Raw conversation text (typically from extract_bookends).
        max_facts: Maximum number of candidates to return.

    Returns:
        List of Candidate objects, ordered by extraction order.
    """
    if not text:
        return []

    candidates: List[Candidate] = []
    seen_spans: set = set()

    sentences = _split_sentences(text)
    tags_for_text = _detect_domain_tags(text)

    for sent in sentences:
        sent_stripped = sent.strip()
        sent_lower = sent_stripped.lower()

        if not sent_lower or len(sent_lower) < 15:
            continue

        span_key = hash(sent_lower)
        if span_key in seen_spans:
            continue

        confidence = 0.0
        category = "general"

        # --- Score by pattern match ---
        if _MILESTONE.search(sent):
            confidence = max(confidence, 0.70)
            category = "milestone"

        if _PROJECT_FACT.search(sent):
            confidence = max(confidence, 0.65)
            if category == "general":
                category = "project"

        if _DECISION.search(sent):
            confidence = max(confidence, 0.55)

        if _PREFERENCE.search(sent):
            confidence = max(confidence, 0.50)
            category = "preference"

        # --- Low-confidence heuristic for long multi-domain sentences ---
        if confidence < 0.3 and len(sent_lower) > 80:
            domain_hits = sum(
                1 for pat in _DOMAIN_MARKERS.values() if pat.search(sent)
            )
            if domain_hits >= 2:
                confidence = 0.35

        if confidence < 0.3:
            continue  # skip noise

        # Build tag list
        tags = _build_tags(text=sent, base_tags=tags_for_text)

        # Clip long sentences
        clipped = sent_stripped[:600]

        candidates.append(
            Candidate(
                text=clipped,
                category=category,
                tags=tags,
                confidence=round(confidence, 2),
            )
        )
        seen_spans.add(span_key)

        if len(candidates) >= max_facts:
            break

    return candidates


# ---------------------------------------------------------------------------
# Dedup logic
# ---------------------------------------------------------------------------

def is_near_dup(text: str, retriever, threshold: float = 0.7) -> Optional[int]:
    """Check if *text* is a near-duplicate of an existing fact.

    Uses Jaccard similarity on word tokens.  Returns the existing
    fact_id if any result scores >= threshold, otherwise None.

    Args:
        text:      Candidate text to check.
        retriever: An active FactRetriever instance.
        threshold: Jaccard similarity threshold (0.0–1.0).

    Returns:
        Existing fact_id or None.
    """
    results = retriever.search(text, limit=5)
    if not results:
        return None

    query_tokens = _tokenize(text)
    if not query_tokens:
        return None

    for r in results:
        content_tokens = _tokenize(r["content"])
        jaccard = _jaccard(query_tokens, content_tokens)
        if jaccard >= threshold:
            return r["fact_id"]

    return None


def existing_exact(text: str, store) -> Optional[int]:
    """Check if an exact duplicate already exists in the facts table.

    Args:
        text:  Candidate text (exact match on stripped content).
        store: An active MemoryStore instance.

    Returns:
        Existing fact_id or None.
    """
    rows = store._conn.execute(
        "SELECT fact_id FROM facts WHERE content = ?", (text.strip(),)
    ).fetchall()
    return rows[0]["fact_id"] if rows else None


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------

def write_candidate(cand: Candidate, store, retriever) -> dict:
    """Write a single Candidate to the fact store, with dedup logic.

    Decision flow:
      1. Exact duplicate → skip.
      2. Near-duplicate  → promote (+0.05 trust) rather than add.
      3. Otherwise       → add at 0.60 trust, then step down to 0.45
                            (ambient facts start low to avoid polluting
                            retrieval with unverified content).

    Args:
        cand:      The Candidate to write.
        store:     An active MemoryStore instance.
        retriever: An active FactRetriever instance.

    Returns:
        Dict with keys: action ('added'|'promoted'|'skipped-exact-dup'),
        fact_id, and trust_before (previous trust, if any).
    """
    # 1. Exact duplicate check
    existing = existing_exact(cand.text, store)
    if existing:
        return {
            "action": "skipped-exact-dup",
            "fact_id": existing,
            "trust_before": None,
        }

    # 2. Near-duplicate check — promote instead of adding
    near_id = is_near_dup(cand.text, retriever)
    if near_id:
        row = store._conn.execute(
            "SELECT trust_score FROM facts WHERE fact_id = ?", (near_id,)
        ).fetchone()
        old_trust = row["trust_score"] if row else None
        store.update_fact(near_id, trust_delta=0.05)
        return {
            "action": "promoted",
            "fact_id": near_id,
            "trust_before": old_trust,
        }

    # 3. New fact — add at 0.60, then step down to 0.45
    tags_str = ",".join(cand.tags)
    fid = store.add_fact(cand.text, category=cand.category, tags=tags_str)
    store.update_fact(fid, trust_delta=-0.15)
    return {"action": "added", "fact_id": fid, "trust_before": 0.6}


def capture_from_messages(
    messages: List[dict],
    store=None,
    retriever=None,
    db_path: Optional[str] = None,
    max_facts: int = 10,
) -> List[dict]:
    """Full capture pipeline: extract → dedup → write.

    Accepts either an existing store+retriever (on_session_end hook) or
    a db_path (cron sweep).  Each result dict includes the Candidate so
    callers can inspect what was captured.

    Args:
        messages:  List of dicts with 'role' and 'content' keys.
        store:     Pre-existing MemoryStore (optional).
        retriever: Pre-existing FactRetriever (optional).
        db_path:   Path to memory_store.db (used when store is None).
        max_facts: Max candidates to extract.

    Returns:
        List of result dicts, each with keys: action, fact_id,
        trust_before, and candidate.
    """
    if store is None:
        store = get_store(db_path)
    if retriever is None:
        retriever = get_retriever(store)

    text = extract_bookends(messages)
    candidates = extract_candidates(text, max_facts=max_facts)

    results = []
    for cand in candidates:
        result = write_candidate(cand, store, retriever)
        result["candidate"] = cand
        results.append(result)

    captured = sum(
        1 for r in results if r["action"] in ("added", "promoted")
    )
    if captured:
        logger.info(
            "Ambient capture: %d facts from %d candidates (%s)",
            captured,
            len(candidates),
            ", ".join(
                f"#{r['fact_id']}:{r['action']}"
                for r in results
                if r["action"] in ("added", "promoted")
            ),
        )

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _split_sentences(text: str) -> List[str]:
    """Naive sentence splitter — adequate for bookend candidate extraction."""
    import re as _re

    parts = _re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if len(p.strip()) > 10]


def _tokenize(text: str) -> set:
    """Tokenize text into a set of cleaned lowercase words (for Jaccard)."""
    tokens = set()
    for w in text.lower().split():
        cleaned = w.strip(".,;:!?'\"()[]{}#@<>/\\-–—")
        if cleaned and len(cleaned) > 1:
            tokens.add(cleaned)
    return tokens


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _detect_domain_tags(text: str) -> List[str]:
    """Return sorted list of domain tags whose patterns fire on *text*."""
    tags = set()
    for domain, pattern in _DOMAIN_MARKERS.items():
        if pattern.search(text):
            tags.add(domain)
    return sorted(tags)


def _build_tags(text: str, base_tags: List[str]) -> List[str]:
    """Build complete tag list: detected domains + expansion tags + 'ambient'."""
    tags = set(base_tags)
    tags.add("ambient")

    for domain in base_tags:
        if domain in _EXPANSION_MAP:
            tags.update(_EXPANSION_MAP[domain])

    return sorted(tags)


# ---------------------------------------------------------------------------
# CLI entry point (for dry-run testing and cron sweep)
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for ambient_memory capture.

    Usage:
        echo '[{"role":"user","content":"..."},...]' | python capture.py --dry-run
        python capture.py --messages session.json --db ~/.hermes/memory_store.db
    """
    import argparse

    parser = argparse.ArgumentParser(description="Ambient memory capture")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidates only; do not write to the fact store.",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to memory_store.db (default: ~/.hermes/memory_store.db)",
    )
    parser.add_argument(
        "--messages",
        type=argparse.FileType("r"),
        help="JSON file containing a messages array.",
    )
    parser.add_argument(
        "--max-facts",
        type=int,
        default=10,
        help="Maximum candidates to extract (default: 10).",
    )
    args = parser.parse_args()

    # Load messages from file or stdin
    if args.messages:
        messages = json.load(args.messages)
    else:
        messages = json.load(sys.stdin)

    # Optionally skip store creation for dry-run
    if args.dry_run:
        store = None
        retriever = None
    else:
        store = get_store(args.db)
        retriever = get_retriever(store)

    text = extract_bookends(messages)
    candidates = extract_candidates(text, max_facts=args.max_facts)

    # Always print candidate summary
    print(
        json.dumps(
            {
                "text_length": len(text),
                "candidates": len(candidates),
                "dry_run": args.dry_run,
                "results": [
                    {
                        "text": c.text[:80],
                        "category": c.category,
                        "tags": c.tags,
                        "confidence": c.confidence,
                    }
                    for c in candidates
                ],
            },
            indent=2,
        )
    )

    # If not dry-run, actually write
    if not args.dry_run and candidates:
        results = capture_from_messages(
            messages,
            store=store,
            retriever=retriever,
            max_facts=args.max_facts,
        )
        # Strip non-serializable Candidate object for clean JSON
        serializable = [{k: v for k, v in r.items() if k != "candidate"} for r in results]
        print(json.dumps({"writes": serializable}, indent=2))


if __name__ == "__main__":
    main()
