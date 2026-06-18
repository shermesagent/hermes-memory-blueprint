# Merge Guide — Applying Config Patches

Three ways to apply the memory configuration patches. Choose the one that
matches your setup.

---

## Method 1: Automated setup.sh (Recommended)

```bash
cd ~/src/hermes-memory-blueprint
./setup.sh
```

The script detects whether the patches are already applied, backs up your
config, and merges cleanly. It asks before touching `config.yaml`.

If you only want to preview what it would do:

```bash
./setup.sh --dry-run
```

---

## Method 2: Manual edit

Open `~/.hermes/config.yaml` and add the sections below. Placement matters:
the `memory:` key is a top-level section (sibling to `model:`, `agent:`,
`terminal:`, etc.). The `plugins:` key is also top-level — if you already
have a `plugins:` block, merge `hermes-memory-store` into it.

### Before (typical config, memory not present)

```yaml
model:
  provider: deepseek
  default: deepseek-v4-flash
agent:
  max_turns: 90
# ... more keys ...
```

### After — add the memory block

```yaml
model:
  provider: deepseek
  default: deepseek-v4-flash
agent:
  max_turns: 90
# ... more keys ...

memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200
  user_char_limit: 1375
  provider: holographic
  nudge_interval: 10
  flush_min_turns: 6
```

### Add the plugin under an existing `plugins:` block

**Before:**

```yaml
plugins:
  enabled:
    - observability/langfuse
  disabled: []
```

**After:**

```yaml
plugins:
  enabled:
    - observability/langfuse
  disabled: []
  hermes-memory-store:
    db_path: $HERMES_HOME/memory_store.db
```

If you have **no** `plugins:` key at all, add the entire block at the root
level:

```yaml
# ... other root keys ...

plugins:
  hermes-memory-store:
    db_path: $HERMES_HOME/memory_store.db
```

After editing, validate:

```bash
hermes config validate
```

---

## Method 3: Merge with `yq`

If you prefer programmatic merging and have [`yq`](https://github.com/mikefarah/yq)
installed:

```bash
# Apply memory section
yq eval-all '. as $item ireduce ({}; . * $item)' \
  ~/.hermes/config.yaml \
  ~/src/hermes-memory-blueprint/config-patches/01-memory.yaml \
  > ~/.hermes/config.yaml.merged

# Apply holographic plugin (deep-merge into existing plugins)
yq eval-all '. as $item ireduce ({}; . * $item)' \
  ~/.hermes/config.yaml.merged \
  ~/src/hermes-memory-blueprint/config-patches/02-holographic.yaml \
  > ~/.hermes/config.yaml

# Clean up and validate
rm ~/.hermes/config.yaml.merged
hermes config validate
```

> **Note:** `yq` deep-merges by default with `*`. If you have custom plugin
> settings you want to preserve, inspect the merged result before replacing
> your live config.

---

## Verifying the patches took effect

```bash
# Check memory section
hermes config get memory

# Check plugin
hermes config get plugins.hermes-memory-store
```

Both should return the values from the patch files.
