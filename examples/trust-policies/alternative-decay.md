# Alternative Trust Decay Policies

`consolidate.py` implements the trust decay and promotion logic. This example shows
how to modify the decay policy for different use cases.

## Default Policy (Reference)

```python
# Default decay in consolidate.py
DECAY_RATE_PER_WEEK = -0.02
DECAY_GRACE_PERIOD_DAYS = 30
TRUST_FLOOR = 0.31
PROMOTION_PER_HELPFUL = 0.05
AMBIENT_STEP_DOWN = -0.15
DEFAULT_TRUST = 0.6
MIN_VISIBLE_TRUST = 0.3
```

Facts idle for 30+ days lose 0.02/week, bottoming at 0.31. Helpful feedback grants
+0.05 per increment.

---

## Policy A: Aggressive Decay

**Use case**: High-volume environments where many facts are captured but most are
noise. You want facts to decay quickly unless actively reinforced.

### Changes

```python
# Aggressive decay — consolidate.py modifications
DECAY_RATE_PER_WEEK = -0.05       # Was -0.02 — 2.5x faster decay
DECAY_GRACE_PERIOD_DAYS = 14      # Was 30 — stales in 2 weeks, not 30 days
TRUST_FLOOR = 0.25                # Was 0.31 — allows falling below visibility
MIN_VISIBLE_TRUST = 0.3           # Unchanged — still invisible below 0.3
PROMOTION_PER_HELPFUL = 0.07      # Was 0.05 — slightly higher reward for survival
AMBIENT_STEP_DOWN = -0.20         # Was -0.15 — higher bar for ambient facts
```

### Trust Trajectory Comparison

| Weeks Idle | Default (from 0.45) | Aggressive (from 0.40) |
|------------|---------------------|------------------------|
| 0 | 0.45 | 0.40 |
| 2 | 0.45 | 0.40 |
| 4 | 0.43 | 0.30 (invisible) |
| 6 | 0.39 | 0.25 (floor) |
| 8 | 0.35 | 0.25 |
| 12 | 0.31 (floor) | 0.25 |

### When to Use

- High-churn projects where facts go stale quickly.
- Environments with noisy capture (many false positives).
- You prefer "show me only what's recently relevant" behavior.
- The agent frequently queries the fact store (so good facts get promoted naturally).

### Implementation

Edit `~/.hermes/profiles/default/scripts/consolidate.py`:

```python
# Near the top of consolidate.py, find the configuration block
# Replace with:

# --- Aggressive decay configuration ---
DECAY_RATE_PER_WEEK = -0.05
DECAY_GRACE_PERIOD_DAYS = 14
TRUST_FLOOR = 0.25
PROMOTION_PER_HELPFUL = 0.07
AMBIENT_STEP_DOWN = -0.20
# --- End aggressive config ---
```

Or better, extract to a config file and load at runtime:

```python
# consolidate.py — load from config
import json

with open(os.path.expanduser('~/.hermes/profiles/default/trust_config.json')) as f:
    config = json.load(f)

DECAY_RATE_PER_WEEK = config.get('decay_rate_per_week', -0.02)
DECAY_GRACE_PERIOD_DAYS = config.get('decay_grace_period_days', 30)
TRUST_FLOOR = config.get('trust_floor', 0.31)
# ...
```

Then `trust_config.json`:
```json
{
  "policy": "aggressive",
  "decay_rate_per_week": -0.05,
  "decay_grace_period_days": 14,
  "trust_floor": 0.25,
  "promotion_per_helpful": 0.07,
  "ambient_step_down": -0.20,
  "min_visible_trust": 0.3
}
```

---

## Policy B: Conservative Decay

**Use case**: Long-lived projects where knowledge stays relevant for months. You want
facts to persist unless explicitly contradicted.

### Changes

```python
# Conservative decay
DECAY_RATE_PER_WEEK = -0.01       # Was -0.02 — half-speed decay
DECAY_GRACE_PERIOD_DAYS = 60      # Was 30 — 60 days before decay starts
TRUST_FLOOR = 0.35                # Was 0.31 — never drops below visibility
MIN_VISIBLE_TRUST = 0.3           # Unchanged
PROMOTION_PER_HELPFUL = 0.03      # Was 0.05 — slower promotion (fewer facts compete)
AMBIENT_STEP_DOWN = -0.10         # Was -0.15 — gentler ambient penalty
```

### Trust Trajectory Comparison

| Weeks Idle | Default (from 0.45) | Conservative (from 0.50) |
|------------|---------------------|--------------------------|
| 0 | 0.45 | 0.50 |
| 4 | 0.43 | 0.50 |
| 8 | 0.35 | 0.50 |
| 12 | 0.31 (floor) | 0.49 |
| 16 | 0.31 | 0.48 |
| 24 | 0.31 | 0.40 |
| 52 | 0.31 | 0.35 (floor) |

### When to Use

- Stable, long-running projects (libraries, platforms, infrastructure).
- Low conversation volume — few opportunities for reinforcement.
- Facts tend to be structural (architecture, dependencies) rather than ephemeral.
- You want to minimize re-capture of the same information.

### Implementation

Same approach — edit `consolidate.py` or use a config file:

```json
{
  "policy": "conservative",
  "decay_rate_per_week": -0.01,
  "decay_grace_period_days": 60,
  "trust_floor": 0.35,
  "promotion_per_helpful": 0.03,
  "ambient_step_down": -0.10,
  "min_visible_trust": 0.3
}
```

---

## Policy C: No Decay (Archive Mode)

**Use case**: A completed project where you want the fact store to serve as a
permanent knowledge base. No decay, no pruning.

```python
DECAY_RATE_PER_WEEK = 0            # No decay
DECAY_GRACE_PERIOD_DAYS = 99999    # Effectively never
TRUST_FLOOR = 0.45                 # Everything stays visible
AMBIENT_STEP_DOWN = 0              # No penalty for auto-capture
```

**Warning**: With no decay, the fact store grows monotonically. Combine with manual
purging and dedup (see [maintenance.md](../../docs/maintenance.md)).

---

## Applying and Verifying

After changing the decay config:

1. **Run consolidate manually** to apply the new policy to existing facts:
   ```bash
   source ~/.hermes/venv/bin/activate
   python3 ~/.hermes/scripts/consolidate.py
   ```

2. **Verify trust distribution shifted:**
   ```sql
   SELECT
       CASE
           WHEN trust_score >= 0.70 THEN 'high'
           WHEN trust_score >= 0.45 THEN 'active'
           WHEN trust_score >= 0.31 THEN 'decaying'
           ELSE 'invisible'
       END AS range,
       COUNT(*) AS count
   FROM facts
   GROUP BY range
   ORDER BY MIN(trust_score) DESC;
   ```

3. **Check cron schedule** — consolidate runs weekly by default. The new policy takes
   full effect after the next scheduled run.

## Related Documents

- [Trust Mechanics](../../docs/trust-mechanics.md) — how trust scores work
- [Maintenance](../../docs/maintenance.md) — auditing trust distribution
- [SQLite Query Reference](../../docs/sqlite-queries.md) — inspecting trust state
