# Troubleshooting

Common failure modes in the Hermes ambient memory system and how to diagnose them.

## Sweep Finds No Sessions

**Symptom**: `sweep.py` cron job runs but produces zero new facts. Logs show
"0 sessions found" or "state.db empty."

**Causes & Fixes**:

1. **`state.db` path wrong or missing.**
   ```bash
   # Check if state.db exists and has sessions
   ls -la ~/.hermes/profiles/default/state.db
   sqlite3 ~/.hermes/profiles/default/state.db \
     "SELECT COUNT(*) FROM sessions;"
   ```
   If it returns 0 or the file doesn't exist, `sweep.py` has nothing to process.
   Verify that `sweep.py` is looking at the correct profile path in its config.

2. **Sessions already processed.**
   `sweep.py` tracks which sessions it has already captured. If all sessions have
   been processed via the `on_session_end` hook, sweep correctly finds nothing.
   ```sql
   -- Check capture log
   SELECT * FROM capture_log ORDER BY captured_at DESC LIMIT 10;
   ```

3. **Cron environment missing PATH.**
   Cron runs with a minimal environment. `sweep.py` may fail because `python3` or
   `sqlite3` isn't on the cron PATH.
   ```cron
   # Bad
   0 */4 * * * sweep.py
   # Good
   0 */4 * * * /usr/bin/python3 /home/user/.hermes/scripts/sweep.py
   ```

## capture.py Import Errors

**Symptom**: `ModuleNotFoundError` for `hermes`, `fact_store`, or other internal
modules.

**Fix**: `capture.py` must run inside the Hermes virtual environment.
```bash
# Find the venv
ls ~/.hermes/venv/bin/python3

# Run with correct interpreter
~/.hermes/venv/bin/python3 ~/.hermes/scripts/capture.py

# Or activate first
source ~/.hermes/venv/bin/activate
python3 ~/.hermes/scripts/capture.py
```

If the venv doesn't exist, Hermes may not have been fully installed. Re-run the
Hermes setup/bootstrap script.

## Cron "Script Must Be Relative to ~/.hermes/scripts/" Error

**Symptom**: Cron job fails with error about script path not being relative.

**Fix**: Hermes cron enforces that scripts live under `~/.hermes/scripts/`. The
cron entry must reference the script by its path relative to that directory:
```cron
# Wrong — absolute path
0 */4 * * * /home/user/.hermes/scripts/sweep.py
# Correct — relative to scripts/
0 */4 * * * sweep.py
```

Hermes' cron runner prepends `~/.hermes/scripts/` to the command.

## SQLITE_BUSY from Concurrent Agent + Cron

**Symptom**: `capture.py` fails with `sqlite3.OperationalError: database is locked`
or `SQLITE_BUSY`.

**Cause**: The agent (using `fact_store` or `session_search` tools) is writing to
`memory_store.db` at the same time `capture.py` or `consolidate.py` tries to write.

**Fixes**:
1. **Add retry logic**. Ensure `capture.py` and `consolidate.py` use WAL mode and
   have a retry loop:
   ```python
   conn.execute("PRAGMA journal_mode=WAL;")
   conn.execute("PRAGMA busy_timeout=5000;")  # 5 second timeout
   ```
2. **Stagger cron**. Don't run sweep at the same time as consolidate.
   ```cron
   0 */4 * * * sweep.py
   30 3 * * 0 consolidate.py  # Sunday at 3:30 AM, not on the hour
   ```
3. **Check for long-running transactions**:
   ```sql
   -- In another sqlite3 session
   PRAGMA busy_timeout;
   ```

## FTS5 Index Stale After Bulk Deletes

**Symptom**: FTS5 search returns results for facts that have been deleted, or
misses newly inserted facts.

**Cause**: The FTS5 content table is out of sync with the main `facts` table.
FTS5 doesn't auto-delete from its index when rows are deleted from the content
table unless triggers are set up.

**Fix**: Rebuild the FTS5 index (see [sqlite-queries.md](sqlite-queries.md#rebuild-fts5-indexes)):
```sql
INSERT INTO facts_fts(facts_fts) VALUES('rebuild');
INSERT INTO session_transcripts_fts(session_transcripts_fts) VALUES('rebuild');
```

## All Entity Types "Unknown"

**Symptom**: Facts show `entity_type: "unknown"` for all entities. Entity
classification fails.

**Status**: This is a **planned limitation** in the current version. Entity type
classification (person, project, tool, concept, etc.) is not yet implemented.
All entities default to `"unknown"`.

**Workaround**: Use domain tags (`tech`, `project`, `pref`, etc.) for filtering
instead of entity types. Entity type classification is on the roadmap.

## Trust Scores Flat at 0.6

**Symptom**: All facts have trust = 0.6. No decay, no promotions, no feedback
history.

**Causes & Fixes**:

1. **No feedback training happening.** Trust mechanics require the `consolidate.py`
   cron job to run. Check if it's scheduled:
   ```bash
   crontab -l | grep consolidate
   ```
   If missing, add it:
   ```cron
   30 3 * * 0 consolidate.py  # Weekly on Sunday
   ```

2. **`consolidate.py` is failing silently.** Check logs:
   ```bash
   tail -50 ~/.hermes/logs/consolidate.log
   ```

3. **No `helpful_count` data.** Promotion requires the agent to increment the
   `helpful_count` when it uses a fact. If the agent never queries the fact store
   or the feedback flow isn't wired up, counts stay at 0.
   - Verify the agent is actually calling `fact_store` tool.
   - Check that `fact_store` results include metadata for feedback.

4. **Ambient step-down not applied.** If facts were bulk-imported or created
   outside `capture.py`, the -0.15 ambient step may have been skipped.
   ```sql
   -- Check for facts without the ambient flag
   SELECT COUNT(*) FROM facts
   WHERE json_extract(metadata, '$.ambient_tracked') IS NULL;
   ```

## Related Documents

- [Architecture Overview](architecture.md) — system design
- [Maintenance Procedures](maintenance.md) — routine health checks
- [SQLite Query Reference](sqlite-queries.md) — diagnostic queries
- [Ambient Capture](ambient-capture.md) — capture pipeline internals
