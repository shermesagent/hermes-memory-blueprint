# SQLite Query Reference

Direct SQL queries against `memory_store.db` for diagnostics, auditing, and manual
maintenance. The database lives at:
```
~/.hermes/profiles/default/memory_store.db
```

## Schema Inspection

```sql
-- List all tables
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;

-- Schema for facts table
SELECT sql FROM sqlite_master WHERE name='facts';

-- Schema for FTS virtual tables
SELECT sql FROM sqlite_master WHERE name LIKE '%_fts%';

-- List all columns in facts
PRAGMA table_info(facts);
```

## Category & Tag Distribution

```sql
-- Count facts by domain tag (stored in tags JSON array)
SELECT
    json_each.value AS tag,
    COUNT(*) AS count
FROM facts, json_each(facts.tags)
GROUP BY tag
ORDER BY count DESC;

-- Count facts by entity (top 20)
SELECT entity, COUNT(*) AS fact_count
FROM facts
GROUP BY entity
ORDER BY fact_count DESC
LIMIT 20;
```

## Fact Count by Trust Range

```sql
SELECT
    CASE
        WHEN trust_score >= 0.70 THEN '0.70-1.00 (high)'
        WHEN trust_score >= 0.45 THEN '0.45-0.69 (active)'
        WHEN trust_score >= 0.31 THEN '0.31-0.44 (decaying)'
        ELSE '0.00-0.30 (invisible)'
    END AS trust_range,
    COUNT(*) AS fact_count,
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM facts), 1) AS pct
FROM facts
GROUP BY trust_range
ORDER BY MIN(trust_score) DESC;
```

## Tag Search

```sql
-- Find facts with a specific tag
SELECT entity, attribute, value, trust_score, tags
FROM facts
WHERE tags LIKE '%"tech"%'
ORDER BY trust_score DESC
LIMIT 20;

-- Find facts matching multiple tags (AND)
SELECT entity, attribute, value, trust_score, tags
FROM facts
WHERE tags LIKE '%"tech"%'
  AND tags LIKE '%"project"%'
ORDER BY trust_score DESC;

-- Find facts matching any of several tags (OR)
SELECT entity, attribute, value, trust_score, tags
FROM facts
WHERE tags LIKE '%"tech"%'
   OR tags LIKE '%"data"%'
ORDER BY trust_score DESC
LIMIT 30;
```

## Entity-to-Fact Mapping

```sql
-- All facts for a specific entity
SELECT attribute, value, trust_score, tags,
       json_extract(metadata, '$.session_id') AS session_id,
       datetime(created_at) AS created
FROM facts
WHERE entity = 'my_project'
ORDER BY trust_score DESC;

-- Entities with the most facts
SELECT entity, COUNT(*) AS total_facts,
       ROUND(AVG(trust_score), 3) AS avg_trust
FROM facts
GROUP BY entity
HAVING total_facts > 1
ORDER BY total_facts DESC;
```

## FTS5 Full-Text Search

```sql
-- Search fact values for a keyword
SELECT entity, attribute, value, trust_score,
       snippet(facts_fts, 1, '<mark>', '</mark>', '...', 32) AS snippet
FROM facts_fts
WHERE facts_fts MATCH 'postgresql'
ORDER BY rank
LIMIT 15;

-- Search with AND/OR operators
SELECT entity, attribute, value, trust_score
FROM facts_fts
WHERE facts_fts MATCH 'svelte OR react'
ORDER BY rank
LIMIT 15;

-- Search session transcripts
SELECT session_id,
       snippet(session_transcripts_fts, 2, '<mark>', '</mark>', '...', 64) AS snippet
FROM session_transcripts_fts
WHERE session_transcripts_fts MATCH 'error OR exception OR traceback'
ORDER BY rank
LIMIT 10;
```

## Purge Stale / Low-Trust Facts

```sql
-- Preview: facts below trust threshold, idle > 90 days
SELECT id, entity, attribute, value, trust_score,
       CAST(julianday('now') - julianday(last_updated) AS INTEGER) AS days_idle
FROM facts
WHERE trust_score < 0.31
  AND julianday('now') - julianday(last_updated) > 90
ORDER BY days_idle DESC;

-- Delete (run after reviewing preview)
DELETE FROM facts
WHERE trust_score < 0.31
  AND julianday('now') - julianday(last_updated) > 90;

-- Delete all invisible facts (trust < 0.3)
DELETE FROM facts WHERE trust_score < 0.3;
```

## Rebuild FTS5 Indexes

```sql
-- Rebuild facts FTS index
INSERT INTO facts_fts(facts_fts) VALUES('rebuild');

-- Rebuild session transcripts FTS index
INSERT INTO session_transcripts_fts(session_transcripts_fts) VALUES('rebuild');

-- Verify row counts match
SELECT 'facts' AS tbl, COUNT(*) FROM facts
UNION ALL
SELECT 'facts_fts', COUNT(*) FROM facts_fts
UNION ALL
SELECT 'session_transcripts', COUNT(*) FROM session_transcripts
UNION ALL
SELECT 'session_transcripts_fts', COUNT(*) FROM session_transcripts_fts;
```

## Session Diagnostics

```sql
-- Recent sessions
SELECT session_id, datetime(created_at) AS created,
       message_count, char_length(content) AS content_chars
FROM session_transcripts
ORDER BY created_at DESC
LIMIT 10;

-- Sessions per day (last 30 days)
SELECT date(created_at) AS day, COUNT(*) AS sessions
FROM session_transcripts
WHERE created_at > datetime('now', '-30 days')
GROUP BY day
ORDER BY day DESC;

-- Facts captured per session (top sessions)
SELECT json_extract(metadata, '$.session_id') AS session_id,
       COUNT(*) AS facts_captured
FROM facts
GROUP BY session_id
ORDER BY facts_captured DESC
LIMIT 20;
```

## Related Documents

- [Maintenance Procedures](maintenance.md) — monthly health checks using these queries
- [Trust Mechanics](trust-mechanics.md) — interpreting trust scores
- [Troubleshooting](troubleshooting.md) — diagnosing problems via SQL
