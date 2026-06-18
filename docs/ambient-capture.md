# Ambient Capture Pipeline (`capture.py`)

`capture.py` is the extraction engine that converts raw conversation transcripts into
structured fact triples. It runs on two triggers: the `on_session_end` hook (immediate)
and `sweep.py` (batch, every 4 hours from cron).

## Extraction Phases

### 1. Bookend Extraction

Only the most information-dense messages are scanned: the **first 3** and **last 3**
user/assistant message pairs from the conversation. The middle of long conversations
tends to be repetitive scaffolding. Bookend extraction catches:

- **First 3**: Problem setup, context establishment, project identification.
- **Last 3**: Decisions made, conclusions reached, preferences revealed.

### 2. Sentence-Level Regex Scanning

Each message is split into sentences. Each sentence is tested against a bank of
regex patterns organized by category:

| Category | Example Pattern | What It Captures |
|----------|----------------|-------------------|
| Milestone | `(?:completed|finished|done|ready)` | "I finished the auth module" |
| Preference | `(?:prefer|like|want|don't|never)` | "I prefer TypeScript over JavaScript" |
| Decision | `(?:decided|chose|going\s+with|settled\s+on)` | "We decided on PostgreSQL" |
| Project Fact | `(?:project|repo|codebase|app)\s+.*\b(?:is|uses|runs\s+on|built\s+with)` | "The project is built with SvelteKit" |
| Tech Stack | `(?:using|built\s+with|runs\s+on|powered\s+by)` | "Using Redis for caching" |
| Constraint | `(?:must|should|can't|cannot|required)` | "The response must be under 200ms" |
| User Role | `(?:I(?:'m|\s+am)\s+(?:a|an|the)\s+\w+)` | "I'm a backend engineer" |

### 3. Confidence Scoring

Each matched sentence receives a confidence score in the range **0.3–0.7**:

- **0.3**: Weak signal — a single regex match with no corroborating context.
- **0.5**: Moderate signal — multiple patterns matched, or explicit language.
- **0.7**: Strong signal — user explicitly stated fact using declarative language
  ("I prefer", "The project uses", "We decided on").

Sentences scoring **below 0.3 are discarded** — they don't become facts.

Confidence is computed as:
```
confidence = base_pattern_score × corroboration_multiplier
```
where `base_pattern_score` depends on which pattern matched and
`corroboration_multiplier` increases when multiple patterns fire on the same sentence
or adjacent sentences.

### 4. Domain Tagging

Each fact is tagged with one or more domains for search recall:

| Tag | Meaning |
|-----|---------|
| `project` | Project-specific fact (repo, dependencies, structure) |
| `pref` | User preference or style choice |
| `tech` | Technology stack component |
| `data` | Data schema, format, or source |
| `constraint` | Hard constraint or requirement |
| `process` | Workflow, methodology, or procedure |
| `context` | Background or domain knowledge |

Facts also receive **expansion tags** — loose synonyms and related terms — to improve
FTS5 recall. For example, a fact tagged `tech` might get expansion tags
`["framework", "library", "dependency", "tool"]`.

### 5. Dedup Strategy

Before insertion, each candidate fact is checked against existing facts:

```
┌──────────────────────────────────────┐
│        Candidate Fact Arrives         │
└─────────────────┬────────────────────┘
                  ▼
        ┌─────────────────┐
        │  Exact Match?   │──── Yes ──► SKIP (duplicate)
        └────────┬────────┘
                 │ No
                 ▼
        ┌─────────────────┐
        │ Jaccard ≥ 0.7?  │──── Yes ──► PROMOTE existing (+0.05 trust)
        └────────┬────────┘
                 │ No
                 ▼
        ┌─────────────────┐
        │   New Fact       │──────────► ADD (trust 0.6, step to 0.45)
        └─────────────────┘
```

**Jaccard similarity** between two facts is computed over their tokenized
representations:

```
J(A, B) = |A ∩ B| / |A ∪ B|
```

A score ≥ 0.7 indicates a near-duplicate — the same fact observed again. Rather than
creating a redundant entry, the existing fact's trust score is incremented by +0.05.

**Default trust on creation**: 0.6, then immediately stepped down by -0.15 to 0.45 for
ambiently captured facts. This "ambient penalty" distinguishes auto-captured facts from
explicitly user-confirmed ones.

## Adding Custom Extraction Patterns

Extraction patterns live in `~/.hermes/profiles/default/scripts/patterns/` as JSON
files. Each file defines a category with named patterns:

```json
{
  "category": "math_notation",
  "patterns": [
    {
      "name": "latex_reference",
      "regex": "\\\\[a-zA-Z]+\\{[^}]+\\}",
      "base_score": 0.45,
      "entity_template": "project",
      "attribute": "uses_notation",
      "tags": ["tech", "data"]
    }
  ]
}
```

Place the file in the patterns directory and `capture.py` auto-loads it on next run.
No restart required — cron-based sweep picks up new patterns automatically.

See [examples/custom-extractors/add-math-patterns.md](../examples/custom-extractors/add-math-patterns.md)
for a complete walkthrough.

## Related Documents

- [Architecture Overview](architecture.md) — where capture fits in the system
- [Trust Mechanics](trust-mechanics.md) — how trust scores evolve after capture
- [Maintenance](maintenance.md) — auditing and cleaning captured facts
