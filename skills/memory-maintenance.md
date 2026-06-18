---
name: memory-maintenance
version: 1.0.0
description: Audit, deduplicate, and maintain Hermes' persistent memory stores — the KV memory, user profile, holographic fact store, and session transcript archive.
category: software-development
platforms: [linux, macos]
---

# Memory Maintenance Skill

Performs a full audit and maintenance pass over all four layers of Hermes' ambient
memory system: KV memory, user profile, fact store, and session transcripts.

## Phase 0: Know the Live System

Before touching anything, load the authoritative documentation:

```
skill_view(name='hermes-agent')
```

Then inspect live state:

```bash
# KV memory and user profile
cat ~/.hermes/profiles/default/memory.json | python3 -m json.tool

# Fact store overview
sqlite3 ~/.hermes/profiles/default/memory_store.db \
  "SELECT COUNT(*) AS total_facts, ROUND(AVG(trust_score),3) AS avg_trust FROM facts;"

# Session transcript count
sqlite3 ~/.hermes/profiles/default/memory_store.db \
  "SELECT COUNT(*) AS sessions, SUM(message_count) AS total_messages FROM session_transcripts;"
```

## Phase 1: Audit

Run a full diagnostic sweep across all stores.

### 1a. KV Memory Budget

```python
import json
with open(os.path.expanduser('~/.hermes/profiles/default/memory.json')) as f:
    mem = json.load(f)

kv = mem.get('kv', {})
total = sum(len(v) for v in kv.values())
pct = total / 22.0  # 2200 chars = 22 * 100%

print(f"KV memory: {total}/2200 chars ({pct:.1f}%)")
for k, v in sorted(kv.items(), key=lambda x: -len(x[1])):
    print(f"  {k}: {len(v)} chars — {v[:80]}...")
```

**Action thresholds:**
- < 40%: Healthy. No action needed.
- 40–60%: Review. Consider consolidating keys.
- > 60%: **Prune required** — go to Phase 2.

### 1b. User Profile Budget

```python
profile = mem.get('user_profile', '')
pct = len(profile) / 13.75  # 1375 chars
print(f"User profile: {len(profile)}/1375 chars ({pct:.1f}%)")
```

### 1c. Fact Store Trust Distribution

```sql
SELECT
    CASE
        WHEN trust_score >= 0.70 THEN 'high (0.70+)'
        WHEN trust_score >= 0.45 THEN 'active (0.45-0.69)'
        WHEN trust_score >= 0.31 THEN 'decaying (0.31-0.44)'
        ELSE 'invisible (<0.31)'
    END AS range,
    COUNT(*) AS count,
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM facts), 1) AS pct
FROM facts
GROUP BY range
ORDER BY MIN(trust_score) DESC;
```

**Healthy target**: 80%+ of facts above 0.31 threshold.

### 1d. Tag Coverage

```sql
SELECT json_each.value AS tag, COUNT(*) AS count
FROM facts, json_each(facts.tags)
GROUP BY tag
ORDER BY count DESC;
```

### 1e. Orphaned Entities

Entities with facts but no recent activity:

```sql
SELECT entity, COUNT(*) as fact_count,
       MAX(julianday('now') - julianday(last_updated)) AS max_days_idle
FROM facts
GROUP BY entity
HAVING max_days_idle > 60
ORDER BY max_days_idle DESC;
```

### 1f. FTS5 Integrity

```sql
SELECT 'facts' AS tbl, COUNT(*) FROM facts
UNION ALL
SELECT 'facts_fts', COUNT(*) FROM facts_fts
UNION ALL
SELECT 'transcripts', COUNT(*) FROM session_transcripts
UNION ALL
SELECT 'transcripts_fts', COUNT(*) FROM session_transcripts_fts;
```

If counts don't match, rebuild FTS5 indexes (Phase 3).

## Phase 2: Prune KV Memory

When KV memory exceeds 60% utilization.

### Decision Matrix: What Goes Where

| Kind of Information | Best Store | Why |
|--------------------|------------|-----|
| Current project path | KV memory | Needed every turn for tool calls |
| User name, role, timezone | KV memory | Needed every turn for context |
| Language/framework preference | KV memory or Profile | If small, KV. If verbose, Profile. |
| Detailed coding style rules | User Profile | Can be wordy; 1,375 char budget |
| Project architecture facts | Fact Store | Query on-demand, unlimited |
| Historical decisions | Fact Store | Entity-linked, searched when relevant |
| Verbatim code snippets | Session Search | FTS5 exact recall |
| Ephemeral task state | Don't store | Use session context |

### Pruning Steps

1. List KV keys sorted by size:
   ```python
   for k, v in sorted(kv.items(), key=lambda x: -len(x[1])):
       print(f"  {len(v):4d} chars — {k}")
   ```

2. For each key, decide:
   - **Keep**: Essential for every-turn context.
   - **Move to Profile**: Wordy preference/context (use `memory` tool user scope).
   - **Move to Fact Store**: Entity-linked knowledge (use `fact_store` tool).
   - **Delete**: Stale, redundant, or no longer relevant.

3. Apply changes via the `memory` tool or directly edit `memory.json`.

4. Verify: re-run the budget check from Phase 1a.

## Phase 3: Dedup Fact Store

### 3a. Find Near-Duplicates

Identify fact pairs with high similarity:

```sql
-- Facts sharing same entity+attribute with different values (potential contradictions)
SELECT a.entity, a.attribute, a.value AS value_old, a.trust_score AS trust_old,
       b.value AS value_new, b.trust_score AS trust_new,
       a.created_at, b.created_at
FROM facts a
JOIN facts b ON a.entity = b.entity AND a.attribute = b.attribute
WHERE a.id < b.id
  AND a.value != b.value
ORDER BY a.entity, a.attribute;
```

### 3b. Resolve Contradictions

For each contradictory pair:
1. If the newer fact is clearly correct → mark the older as unhelpful (-0.10).
2. If uncertain → leave both; the newer will naturally outrank as the older decays.
3. If both are wrong → mark both as unhelpful.

Use `fact_store` tool feedback:
```
fact_store(action='feedback', fact_id='<id>', feedback='unhelpful')
```

### 3c. Purge Stale Facts

```sql
-- Preview
SELECT id, entity, attribute, value, trust_score,
       CAST(julianday('now') - julianday(last_updated) AS INTEGER) AS days_idle
FROM facts
WHERE trust_score < 0.31
  AND julianday('now') - julianday(last_updated) > 90;

-- Execute after review
DELETE FROM facts
WHERE trust_score < 0.31
  AND julianday('now') - julianday(last_updated) > 90;
```

### 3d. Rebuild FTS5

After any bulk delete:

```sql
INSERT INTO facts_fts(facts_fts) VALUES('rebuild');
INSERT INTO session_transcripts_fts(session_transcripts_fts) VALUES('rebuild');
```

## Phase 4: Consolidate User Profile

Review and tighten the user profile:

1. Read current profile:
   ```python
   print(mem.get('user_profile', ''))
   ```

2. Check for:
   - **Redundancy**: Same preference stated multiple ways.
   - **Staleness**: References to completed projects, old roles.
   - **Over-specificity**: Rules that should be fact-store entries.
   - **Contradictions**: "I prefer TypeScript" and "Use plain JavaScript".

3. Rewrite profile to be concise and current. Keep under 1,375 chars.

4. Update via `memory` tool (user scope).

## Phase 5: Obsidian Vault (If Present)

If the user maintains a second brain / Obsidian vault:

1. Check for vault path in KV memory or profile.
2. Cross-reference fact store entities with vault notes.
3. Flag facts that could be promoted to vault entries (high trust, stable).
4. Flag vault entries that could seed new fact-store entities.

## Memory vs. Fact Store Decision Matrix

| Question | Answer → Store |
|----------|---------------|
| Does the agent need this on every single turn? | **Yes** → KV Memory (budget: 2,200 chars) |
| Is it about *me* (user) as a person, not a project? | **Yes** → User Profile (budget: 1,375 chars) |
| Is it a stable fact about a specific entity (project, tool, person)? | **Yes** → Fact Store (unlimited, on-demand) |
| Do I need to search for exact wording later? | **Yes** → Session Search (FTS5, on-demand) |
| Is it ephemeral / only relevant this session? | **Yes** → Don't store (use session context) |
| Is it too large for KV but needed often? | → Split: essentials in KV, details in Fact Store |

## Completion

After all phases, produce a summary:

```
## Memory Maintenance Summary — YYYY-MM-DD

### KV Memory
- Before: X/2200 chars (Y%)
- After:  X/2200 chars (Y%)
- Keys removed: [...]
- Keys consolidated: [...]

### User Profile
- Before: X/1375 chars
- After:  X/1375 chars

### Fact Store
- Total facts: N → N (Δ)
- By trust: high=M, active=N, decaying=O, invisible=P
- Stale facts purged: Q
- Contradictions resolved: R
- FTS5 rebuilt: yes/no

### Recommendations
- [Any follow-up actions needed]
```
