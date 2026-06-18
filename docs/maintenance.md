# Memory Maintenance Procedures

Regular maintenance keeps the ambient memory system healthy, responsive, and
cost-efficient. These procedures should be performed **monthly** or after any
batch import of facts.

## Monthly Checklist

### 1. Check KV Memory Utilization

**Target**: Under 60% of the 2,200-character budget.

```bash
# From ~/.hermes/profiles/default/
python3 -c "
import json
with open('memory.json') as f:
    mem = json.load(f)
total = sum(len(v) for v in mem.get('kv', {}).values())
print(f'KV memory: {total} / 2200 chars ({total/22:.1f}%)')
print(f'Keys: {list(mem.get(\"kv\", {}).keys())}')
"
```

If utilization exceeds 60%:
- Identify the largest keys.
- Consolidate related keys (e.g., merge `project_path_1`, `project_path_2` into a
  single `active_projects` key with JSON array).
- Remove keys for completed or abandoned projects.
- Move verbose preferences to the User Profile (Layer 2, 1,375 char budget).

### 2. Review Fact Store for Stale Entities

Stale facts (trust < 0.31, idle > 90 days) accumulate and slow FTS5 searches.

Use the diagnostic queries in [sqlite-queries.md](sqlite-queries.md) to find:

- Facts below trust threshold
- Entities with no recent activity
- Contradictory facts that may need manual resolution

**Purge command** (review output before deleting):
```sql
-- Preview stale facts
SELECT entity, attribute, value, trust_score,
       julianday('now') - julianday(last_updated) AS days_idle
FROM facts
WHERE trust_score < 0.31 AND days_idle > 90;

-- Delete after review
DELETE FROM facts WHERE trust_score < 0.31
  AND julianday('now') - julianday(last_updated) > 90;
```

### 3. Rebuild FTS5 Index

After bulk deletes or inserts, the FTS5 index may be stale. Rebuild it:

```sql
-- In memory_store.db
INSERT INTO session_transcripts_fts(session_transcripts_fts) VALUES('rebuild');
INSERT INTO facts_fts(facts_fts) VALUES('rebuild');
```

Or use the maintenance command:
```bash
cd ~/.hermes/profiles/default
python3 -c "
import sqlite3
conn = sqlite3.connect('memory_store.db')
conn.execute(\"INSERT INTO session_transcripts_fts(session_transcripts_fts) VALUES('rebuild')\")
conn.execute(\"INSERT INTO facts_fts(facts_fts) VALUES('rebuild')\")
conn.commit()
conn.close()
print('FTS5 indexes rebuilt.')
"
```

### 4. Audit Ambient-Tracked Facts

Review facts flagged as `ambient_tracked` (auto-captured, not user-confirmed):

```sql
SELECT entity, attribute, value, trust_score, tags
FROM facts
WHERE json_extract(metadata, '$.ambient_tracked') = 1
ORDER BY trust_score DESC
LIMIT 50;
```

Look for:
- Facts that should be promoted (mark as `helpful`).
- Facts that are wrong (mark as `unhelpful`).
- Clusters of facts about abandoned topics (purge).

### 5. When to Tweak Regex Patterns

Add or adjust extraction patterns when:
- The agent consistently fails to capture a category of information you care about.
- A pattern is producing too many false positives (low-quality facts cluttering the
  store).
- You start a new domain (e.g., hardware, ML) with domain-specific terminology.

Patterns live in `~/.hermes/profiles/default/scripts/patterns/`. See
[ambient-capture.md](ambient-capture.md#adding-custom-extraction-patterns) for the
format.

### 6. Purge a Bad Batch of Captures

If `capture.py` ingested a noisy session and created many low-quality facts:

```sql
-- Find facts from a specific session
SELECT id, entity, attribute, value FROM facts
WHERE json_extract(metadata, '$.session_id') = '<session_uuid>';

-- Delete that session's facts
DELETE FROM facts
WHERE json_extract(metadata, '$.session_id') = '<session_uuid>';
```

Then rebuild FTS5 (see step 3).

### 7. Check Profile Budget

The User Profile (Layer 2) has a 1,375-character budget. Verify:

```bash
python3 -c "
import json
with open('memory.json') as f:
    mem = json.load(f)
profile = mem.get('user_profile', '')
print(f'Profile: {len(profile)} / 1375 chars ({len(profile)/13.75:.1f}%)')
"
```

## Related Documents

- [SQLite Query Reference](sqlite-queries.md) — all diagnostic queries
- [Architecture Overview](architecture.md) — layer budgets and data flow
- [Troubleshooting](troubleshooting.md) — common maintenance problems
- [Trust Mechanics](trust-mechanics.md) — trust score lifecycle
