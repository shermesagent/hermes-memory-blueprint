# Custom Extractor: Math & LaTeX Patterns

This example shows how to extend `capture.py` with custom regex patterns to capture
mathematical notation, LaTeX references, and formula-related facts from conversations.

## Use Case

You frequently discuss mathematical concepts, formulas, or LaTeX documents. The
default extraction patterns don't recognize math notation, so these facts are missed.
You want facts like:

- "The project uses LaTeX for typesetting"
- "The loss function is cross-entropy"
- "The document references `\eqref{eq:navier-stokes}`"

## Step 1: Create the Pattern File

Create `~/.hermes/profiles/default/scripts/patterns/math_notation.json`:

```json
{
  "category": "math_notation",
  "description": "Mathematical notation, LaTeX, and formula references",
  "patterns": [
    {
      "name": "latex_usage",
      "regex": "(?:using|uses|built\\s+with|written\\s+in)\\s+(?:LaTeX|MathJax|KaTeX)",
      "base_score": 0.50,
      "entity_template": "project",
      "attribute": "typesetting_engine",
      "tags": ["tech", "data"]
    },
    {
      "name": "formula_reference",
      "regex": "\\\\eqref\\{[^}]+\\}|\\\\ref\\{[^}]+\\}|Equation\\s+\\(?\\d+[a-z]?\\)?",
      "base_score": 0.40,
      "entity_template": "document",
      "attribute": "references_formula",
      "tags": ["data", "context"]
    },
    {
      "name": "math_environment",
      "regex": "\\\\begin\\{(equation|align|gather|multline)\\*?\\}",
      "base_score": 0.45,
      "entity_template": "document",
      "attribute": "uses_math_environment",
      "tags": ["tech", "data"]
    },
    {
      "name": "loss_function",
      "regex": "(?:loss\\s+function|objective\\s+function|cost\\s+function)\\s+(?:is|uses|:=|:)\\s*(\\w+(?:[\\s-]\\w+)*)",
      "base_score": 0.55,
      "entity_template": "model",
      "attribute": "loss_function",
      "tags": ["tech", "data", "project"]
    },
    {
      "name": "math_library_usage",
      "regex": "(?:using|import|built\\s+with)\\s+(numpy|scipy|sympy|torch|tensorflow|jax|pytorch)",
      "base_score": 0.50,
      "entity_template": "project",
      "attribute": "math_library",
      "tags": ["tech", "project"]
    },
    {
      "name": "optimizer_reference",
      "regex": "(?:optimizer|optimization\\s+algorithm)\\s+(?:is|uses|:=|:)\\s*(adam|sgd|rmsprop|adagrad|adamw|lbfgs)",
      "base_score": 0.55,
      "entity_template": "model",
      "attribute": "optimizer",
      "tags": ["tech", "project"]
    },
    {
      "name": "metric_reference",
      "regex": "(?:metric|evaluation|scoring)\\s+(?:is|uses|:=|:)\\s*(accuracy|precision|recall|f1|auc|mse|mae|rmse|perplexity)",
      "base_score": 0.50,
      "entity_template": "model",
      "attribute": "evaluation_metric",
      "tags": ["tech", "data"]
    }
  ]
}
```

## Step 2: How `capture.py` Loads Custom Patterns

`capture.py` scans the `patterns/` directory on each run and merges all `.json` files
into its pattern bank. The loading logic (simplified):

```python
# In capture.py — pattern loading
def load_patterns(patterns_dir: str) -> list[dict]:
    patterns = []
    for fname in os.listdir(patterns_dir):
        if fname.endswith('.json'):
            with open(os.path.join(patterns_dir, fname)) as f:
                data = json.load(f)
                patterns.extend(data.get('patterns', []))
    return patterns
```

The merged patterns are compiled into a list of `(compiled_regex, config)` tuples
for the scanning phase.

## Step 3: Before vs. After

### Before (without math patterns)

```
Conversation:
  User: "I'm training a transformer model. The loss function is cross-entropy
         and I'm using the AdamW optimizer."
  Assistant: "Got it. AdamW with cross-entropy loss for a transformer."

capture.py extracts:
  (nothing math-related — no matching patterns)
```

### After (with math_patterns.json installed)

```
capture.py extracts:
  Fact 1: entity="model", attribute="loss_function",
          value="cross-entropy", confidence=0.55, tags=["tech","data","project"]
  Fact 2: entity="model", attribute="optimizer",
          value="adamw", confidence=0.55, tags=["tech","project"]
```

## Step 4: Pattern Design Guidelines

When writing custom patterns:

1. **Use non-capturing groups `(?:...)` for alternation, capturing groups `(...)` for
   the value you want to extract.** The first capturing group becomes the fact value.

2. **Set `base_score` in 0.30–0.70 range.** Start conservative (0.35–0.45) and
   increase if the pattern proves reliable.

3. **Use `entity_template` for entity assignment.** If the entity should come from
   context (e.g., the current project name), use a placeholder like `"project"` and
   `capture.py` will resolve it from session metadata.

4. **Tag generously.** More tags = better FTS5 recall. Include both specific
   (`"math"`, `"latex"`) and broad (`"tech"`, `"data"`) tags.

5. **Test with sample sentences before deploying:**
   ```python
   import re
   pattern = re.compile(r"(?:loss\s+function)\s+(?:is|uses)\s+(\w+(?:[\s-]\w+)*)", re.I)
   test = "The loss function is cross-entropy with label smoothing"
   match = pattern.search(test)
   print(match.group(1) if match else "No match")
   # Output: cross-entropy
   ```

## Step 5: Verify It's Working

After deploying the pattern file, trigger a manual capture to test:

```bash
# Run capture on the latest session
source ~/.hermes/venv/bin/activate
python3 ~/.hermes/scripts/capture.py --latest

# Check for math-tagged facts
sqlite3 ~/.hermes/profiles/default/memory_store.db \
  "SELECT entity, attribute, value, trust_score FROM facts
   WHERE tags LIKE '%data%' AND (attribute LIKE '%math%' OR attribute LIKE '%loss%' OR attribute LIKE '%optimizer%')
   ORDER BY created_at DESC LIMIT 10;"
```

## Related Documents

- [Ambient Capture Pipeline](../../docs/ambient-capture.md) — capture.py internals
- [SQLite Query Reference](../../docs/sqlite-queries.md) — verifying captured facts
- [Maintenance](../../docs/maintenance.md) — pattern tuning guidance
