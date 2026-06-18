# Hermes Memory Blueprint

**Four-layer memory architecture for Hermes Agent.**

Give your Hermes agent ambient memory — automatic fact extraction from conversations, deduplication, trust scoring, and recall. No vector DB, no embeddings API, no external services. Everything runs locally in `~/.hermes/`.

---

## Architecture

```
Conversation  ──►  capture.py  ──►  fact_store (SQLite)  ──►  fact_store tool
  (turns)         (regex→dedup)       (trust-scored rows)      (on-demand search)
```

**Four layers, not one:**

| Layer | Tool/Store | Injected? | Capacity | What it holds |
|-------|-----------|-----------|----------|--------------|
| **KV Memory** | `memory` tool | Every turn | 2,200 chars | User role, project paths, tool preferences |
| **User Profile** | `memory` tool (user) | Every turn | 1,375 chars | Communication preferences, domain context |
| **Fact Store** | `fact_store` tool | On demand | Unlimited (SQLite) | Deep project knowledge, entity-linked facts with trust scores |
| **Session Archive** | `session_search` tool | On demand | Unlimited (FTS5) | Verbatim transcript recall |

The ambient capture pipeline **(this repo)** sits on top of the fact store:

```
on_session_end (plugin hook) ──► capture.py ──► dedup ──► fact_store
state.db ◄── sweep.py (every 4h) ──► capture.py ──► dedup ──► fact_store
                                    consolidate.py (weekly) ──► decay/promote/supersede
```

---

## Quick Start

```bash
git clone https://github.com/Micah-Taylor/hermes-memory-blueprint.git
cd hermes-memory-blueprint
./setup.sh          # interactive install
./setup.sh --yes    # skip confirmations
./setup.sh --dry-run # preview only
```

Restart Hermes or start a new session. Memory capture activates automatically.

---

## How It Works

### 1. Capture (`ambient_memory/capture.py`)

When a conversation ends, the `on_session_end` hook (or the cron sweep) runs the capture pipeline:

1. **Bookend extraction** — Gets the first 3 and last 3 user/assistant messages (the goal + resolution)
2. **Regex scanning** — Searches each sentence for patterns:
   - **Milestones**: "completed", "finished", "launched", "shipped"
   - **Preferences**: "I prefer", "I use", "I want", "I need"
   - **Decisions**: "we decided", "we agreed", "we chose", "the plan is"
   - **Project facts**: paths (`~/src/...`), versions (`v1.2.3`), ports (`:8080`), URLs (`*.org`)
3. **Confidence scoring** — Each match gets a score (0.3–0.7). Low-confidence hits are skipped
4. **Domain tagging** — Content is tagged with domains (project, pref, tech, data) for filtered search

### 2. Dedup (inside `capture.py`)

Every candidate fact passes through two checks before writing:

| Check | Method | Action |
|-------|--------|--------|
| **Exact duplicate** | SQL `content = ?` | Skip (no-op) |
| **Near duplicate** | Jaccard similarity ≥ 0.7 via FTS5 pre-check | Promote existing fact (+0.05 trust) |
| **New fact** | No match found | Add at trust 0.6 → immediately step to 0.45 |

Jaccard similarity measures word-set overlap — it's fast, deterministic, and needs no ML dependencies.

### 3. Storage (`memory_store.db`)

Facts live in a compact SQLite schema:

```
facts: fact_id, content, category, tags, trust_score, retrieval_count,
       helpful_count, created_at, updated_at, hrr_vector (1024-dim BLOB)

entities: entity_id, name, entity_type, aliases
fact_entities: fact_id ←→ entity_id (many-to-many)
facts_fts: FTS5 full-text index
memory_banks: category centroid vectors
```

No external database. No network calls. ~1MB for 1,000 facts with vectors.

### 4. Retrieval (`fact_store` tool)

Search is explicit — you call `fact_store(action='search', query='...')` when you need facts. The search pipeline fuses:

- **FTS5 keyword match** (0.4 weight)
- **Jaccard token overlap** (0.3 weight)
- **HRR vector similarity** (0.3 weight)
- **Trust score multiplier** (higher-trust facts rank above lower-trust ones)

Results are sorted by composite relevance score.

---

## Cron Jobs

| Job | Schedule | What it does |
|-----|----------|-------------|
| `ambient-memory-sweep` | Every 4 hours | Polls `state.db` for ended sessions, runs capture pipeline, writes new facts |
| `ambient-memory-consolidate` | Sunday 3 AM | Three passes: decay stale facts (-0.02/wk after 30d), promote recaptured ones (+0.05), supersede contradictions (-0.10 older) |

Both run as `no_agent` scripts — zero token cost.

---

## Customization

### Adding regex patterns

Edit the module-level constants at the top of `ambient_memory/capture.py`:

```python
_MILESTONE = re.compile(
    r'\b(?:completed|finished|launched|shipped|deployed|migrated)\b', re.IGNORECASE
)

_PREFERENCE = re.compile(
    r'\bI\s+(?:prefer|like|love|hate|use|want|need|always|never)\s+', re.IGNORECASE
)

_DECISION = re.compile(
    r'\b(?:we\s+(?:decided|agreed|chose|settled\s+on|went\s+with)'
    r'|the\s+(?:plan|next\s+step|approach)\s+is)\b', re.IGNORECASE
)

_PROJECT_FACT = re.compile(
    r'(?:~/\S+|/[\w/.-]+|v\d+\.\d+\.\d+|:\d{4,5}|[\w-]+\.(?:org|com|io|app))',
    re.IGNORECASE,
)
```

### Adding domain markers

Extend `_DOMAIN_MARKERS` and `_EXPANSION_MAP`:

```python
_DOMAIN_MARKERS["math"] = re.compile(r'\b(equation|derivative|integral|calculus)\b', re.IGNORECASE)
_EXPANSION_MAP["math"] = ["math", "calculus", "derivative", "formula"]
```

### Tuning trust policies

Modify `ambient_memory/consolidate.py` constants:

| Constant | Default | What it controls |
|----------|---------|-----------------|
| `STALENESS_DAYS` | 30 | Days before an untouched fact starts decaying |
| `DECAY_DELTA` | -0.02 | Trust loss per week of staleness |
| `FLOOR_TRUST` | 0.31 | Minimum trust (can't decay below this) |
| `BOOST_DELTA` | 0.05 | Trust gain per `fact_feedback(action='helpful')` |
| `SUPERSEDE_DELTA` | -0.10 | Trust loss for older fact in a contradiction pair |

---

## Config

Add to `~/.hermes/config.yaml` (setup.sh does this automatically):

```yaml
memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200
  user_char_limit: 1375
  provider: holographic
  nudge_interval: 10
  flush_min_turns: 6

plugins:
  hermes-memory-store:
    db_path: $HERMES_HOME/memory_store.db
```

---

## Requirements

- **Hermes Agent** installed and configured (`~/.hermes/config.yaml` exists)
- **Python 3.11+** (matching Hermes' runtime)
- No additional API keys, no vector DB, no external dependencies

---

## Project Structure

```
hermes-memory-blueprint/
├── setup.sh                    # One-command installer
├── ambient_memory/
│   ├── capture.py              # Extraction + dedup + write pipeline
│   ├── sweep.py                # Cron poller for ended sessions
│   └── consolidate.py          # Weekly trust maintenance
├── scripts/
│   ├── sweep.py                # Thin cron wrapper → ambient_memory.sweep
│   └── consolidate.py          # Thin cron wrapper → ambient_memory.consolidate
├── config-patches/
│   ├── 01-memory.yaml          # memory: section for config.yaml
│   ├── 02-holographic.yaml     # plugins: section for config.yaml
│   └── 03-merge-guide.md       # How to apply patches
├── docs/
│   ├── architecture.md         # Four-layer architecture deep dive
│   ├── ambient-capture.md      # How capture.py works in detail
│   ├── trust-mechanics.md      # Trust scoring, decay, promotion
│   ├── maintenance.md          # Monthly audit procedures
│   ├── sqlite-queries.md       # Useful raw SQL queries
│   └── troubleshooting.md      # Common issues and fixes
├── skills/
│   └── memory-maintenance.md   # Hermes skill for weekly audit
├── examples/
│   ├── custom-extractors/      # Adding your own regex patterns
│   └── trust-policies/         # Alternative decay schedules
├── README.md
└── LICENSE
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Sweep finds no sessions | `state.db` path wrong or DB missing | Start a Hermes session first |
| `Script path must be relative` | Cron script not in `~/.hermes/scripts/` | Run `setup.sh` — it handles this |
| `SQLITE_BUSY` errors | Agent + cron writing concurrently | Schedule maintenance for idle hours |
| All trust scores at 0.6 | No feedback training happening | Use `fact_feedback` or let consolidate (pass 2) handle it |
| FTS5 returns stale results | Direct SQL deletes without rebuild | `INSERT INTO facts_fts(facts_fts) VALUES('rebuild')` |

---

## License

MIT — see [LICENSE](./LICENSE). Copyright 2026 Micah Taylor.
