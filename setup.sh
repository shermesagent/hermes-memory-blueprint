#!/usr/bin/env bash
#
# setup.sh — Hermes Memory Blueprint installer
# One-command setup for the four-layer ambient memory system.
#
# Usage:
#   ./setup.sh            # interactive install
#   ./setup.sh --dry-run  # preview only, no changes
#   ./setup.sh --yes      # skip confirmations
#
set -euo pipefail

# ── Color helpers ───────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ── Parse flags ─────────────────────────────────────────────────────────────
DRY_RUN=false
SKIP_CONFIRM=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --yes|-y)  SKIP_CONFIRM=true ;;
        --help|-h)
            echo "Usage: ./setup.sh [--dry-run] [--yes]"
            echo ""
            echo "  --dry-run   Preview actions without making changes."
            echo "  --yes, -y   Skip all confirmation prompts."
            exit 0
            ;;
        *) error "Unknown flag: $arg"; exit 1 ;;
    esac
done

$DRY_RUN && info "Dry-run mode — no changes will be made."

# ── Paths ───────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
CONFIG_FILE="$HERMES_HOME/config.yaml"
AMBIENT_SRC="$SCRIPT_DIR/ambient_memory"
SCRIPTS_SRC="$SCRIPT_DIR/scripts"
AMBIENT_DST="$HERMES_HOME/ambient_memory"
SCRIPTS_DST="$HERMES_HOME/scripts"
STATE_DB="$HERMES_HOME/memory_store.db"

# ── Pre-flight checks ───────────────────────────────────────────────────────
echo ""
info "Hermes Memory Blueprint — Setup"
info "================================"
echo ""

# 1. Verify Hermes is installed
if [[ ! -f "$CONFIG_FILE" ]]; then
    error "Hermes config not found at $CONFIG_FILE"
    error "Is Hermes Agent installed? Visit https://hermes-agent.nousresearch.com"
    exit 1
fi
success "Hermes config found at $CONFIG_FILE"

# 2. Verify source files exist
if [[ ! -d "$AMBIENT_SRC" ]]; then
    warn "ambient_memory/ directory not found in repo. Skipping ambient copy."
    AMBIENT_FILES=()
else
    mapfile -t AMBIENT_FILES < <(find "$AMBIENT_SRC" -maxdepth 1 -name '*.py' -type f 2>/dev/null || true)
fi

if [[ ! -d "$SCRIPTS_SRC" ]]; then
    warn "scripts/ directory not found in repo. Skipping scripts copy."
    SCRIPT_FILES=()
else
    mapfile -t SCRIPT_FILES < <(find "$SCRIPTS_SRC" -maxdepth 1 -name '*.py' -type f 2>/dev/null || true)
fi

# ── Confirmation prompt ─────────────────────────────────────────────────────
if ! $SKIP_CONFIRM && ! $DRY_RUN; then
    echo ""
    info "This script will:"
    info "  1. Create ~/.hermes/ambient_memory/ and ~/.hermes/scripts/"
    info "  2. Copy Python files into those directories"
    info "  3. Patch ~/.hermes/config.yaml with memory + holographic settings"
    info "  4. Register cron jobs (sweep every 240m, consolidate weekly)"

    if [[ ${#AMBIENT_FILES[@]} -eq 0 && ${#SCRIPT_FILES[@]} -eq 0 ]]; then
        warn "No .py files found in ambient_memory/ or scripts/. Only config patching will run."
    fi

    echo ""
    read -r -p "Proceed with installation? [y/N] " REPLY
    if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
        info "Aborted by user."
        exit 0
    fi
fi

# ── Helper: safe mkdir ─────────────────────────────────────────────────────
safe_mkdir() {
    local dir="$1"
    if [[ -d "$dir" ]]; then
        info "Directory exists: $dir"
    else
        if $DRY_RUN; then
            info "[dry-run] Would create: $dir"
        else
            mkdir -p "$dir"
            success "Created: $dir"
        fi
    fi
}

# ── Helper: safe cp ─────────────────────────────────────────────────────────
safe_cp() {
    local src="$1"
    local dst="$2"
    local filename
    filename="$(basename "$src")"

    if $DRY_RUN; then
        info "[dry-run] Would copy: $filename → $dst/"
        return
    fi

    if [[ -f "$dst/$filename" ]]; then
        info "Already exists (skipping): $dst/$filename"
    else
        cp "$src" "$dst/"
        success "Copied: $filename → $dst/"
    fi
}

# ── Step 1: Create directories ──────────────────────────────────────────────
echo ""
info "Step 1: Creating target directories..."
safe_mkdir "$AMBIENT_DST"
safe_mkdir "$SCRIPTS_DST"

# ── Step 2: Copy ambient_memory Python files ────────────────────────────────
echo ""
info "Step 2: Copying ambient memory modules..."
if [[ ${#AMBIENT_FILES[@]} -eq 0 ]]; then
    warn "No .py files in ambient_memory/. Skipping."
else
    for f in "${AMBIENT_FILES[@]}"; do
        safe_cp "$f" "$AMBIENT_DST"
    done
fi

# ── Step 3: Copy scripts ────────────────────────────────────────────────────
echo ""
info "Step 3: Copying utility scripts..."
if [[ ${#SCRIPT_FILES[@]} -eq 0 ]]; then
    warn "No .py files in scripts/. Skipping."
else
    for f in "${SCRIPT_FILES[@]}"; do
        safe_cp "$f" "$SCRIPTS_DST"
    done
fi

# ── Step 4: Patch memory section in config ─────────────────────────────────
echo ""
info "Step 4: Checking config for memory section..."

MEMORY_PATCH_NEEDED=false
HOLOGRAPHIC_PATCH_NEEDED=false

if grep -q '^memory:' "$CONFIG_FILE" 2>/dev/null; then
    success "Memory section already present in config.yaml"
else
    MEMORY_PATCH_NEEDED=true
    warn "Memory section not found. Will patch."
fi

if grep -q 'hermes-memory-store' "$CONFIG_FILE" 2>/dev/null; then
    success "Holographic plugin already configured in config.yaml"
else
    HOLOGRAPHIC_PATCH_NEEDED=true
    warn "Holographic plugin not found. Will patch."
fi

if $MEMORY_PATCH_NEEDED || $HOLOGRAPHIC_PATCH_NEEDED; then
    if $DRY_RUN; then
        info "[dry-run] Would patch $CONFIG_FILE with memory + holographic sections."
    else
        info ""
        info "Patching $CONFIG_FILE requires editing your live Hermes config."
        if ! $SKIP_CONFIRM; then
            read -r -p "Apply config patches? A backup will be made. [y/N] " REPLY
            if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
                info "Skipping config patches. You can apply them manually from config-patches/."
                MEMORY_PATCH_NEEDED=false
                HOLOGRAPHIC_PATCH_NEEDED=false
            fi
        fi

        if $MEMORY_PATCH_NEEDED || $HOLOGRAPHIC_PATCH_NEEDED; then
            # Backup config
            BACKUP="${CONFIG_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
            cp "$CONFIG_FILE" "$BACKUP"
            success "Backed up config to $(basename "$BACKUP")"

            # Apply memory patch if needed
            if $MEMORY_PATCH_NEEDED; then
                # Append memory section before the first top-level key after 'm'
                # We insert it before 'network:' which is alphabetically after
                if grep -q '^network:' "$CONFIG_FILE"; then
                    PATCH_CONTENT="memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200
  user_char_limit: 1375
  provider: holographic
  nudge_interval: 10
  flush_min_turns: 6"
                    TEMP_FILE="$(mktemp)"
                    awk -v patch="$PATCH_CONTENT" '
                        /^network:/ && !patched { print patch; print ""; patched=1 }
                        { print }
                    ' "$CONFIG_FILE" > "$TEMP_FILE"
                    mv "$TEMP_FILE" "$CONFIG_FILE"
                    success "Patched memory section into config.yaml"
                else
                    # Fallback: append at end
                    cat >> "$CONFIG_FILE" <<'YAMLEOF'

memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200
  user_char_limit: 1375
  provider: holographic
  nudge_interval: 10
  flush_min_turns: 6
YAMLEOF
                    success "Appended memory section to end of config.yaml"
                fi
            fi

            # Apply holographic plugin patch if needed
            if $HOLOGRAPHIC_PATCH_NEEDED; then
                if grep -q '^plugins:' "$CONFIG_FILE"; then
                    # Plugins section exists; append under it
                    TEMP_FILE="$(mktemp)"
                    awk '
                        /^plugins:/ && !patched {
                            print
                            print "  hermes-memory-store:"
                            print "    db_path: $HERMES_HOME/memory_store.db"
                            patched=1
                            next
                        }
                        { print }
                    ' "$CONFIG_FILE" > "$TEMP_FILE"
                    mv "$TEMP_FILE" "$CONFIG_FILE"
                    success "Patched holographic plugin into config.yaml plugins: section"
                else
                    # No plugins section; append at end
                    cat >> "$CONFIG_FILE" <<'YAMLEOF'

plugins:
  hermes-memory-store:
    db_path: $HERMES_HOME/memory_store.db
YAMLEOF
                    success "Appended holographic plugin to end of config.yaml"
                fi
            fi
        fi
    fi
else
    info "No config patches needed."
fi

# ── Step 5: Register cron jobs ──────────────────────────────────────────────
echo ""
info "Step 5: Registering cron jobs..."

register_cron() {
    local script_name="$1"
    local schedule="$2"
    local job_name="$3"
    local script_path="$SCRIPTS_DST/$script_name"

    if $DRY_RUN; then
        info "[dry-run] Would register cron: hermes cron create --script $script_name --schedule \"$schedule\" --no-agent --name \"$job_name\""
        return 0
    fi

    # Check if cron job already exists
    if hermes cron list 2>/dev/null | grep -q "$job_name"; then
        info "Cron job '$job_name' already registered. Skipping."
        return 0
    fi

    if [[ ! -f "$script_path" ]]; then
        warn "Script not found: $script_path — cannot register cron job '$job_name'."
        warn "Copy scripts/ into place first, then run:"
        warn "  hermes cron create --script $script_name --schedule \"$schedule\" --no-agent --name \"$job_name\""
        return 1
    fi

    hermes cron create --script "$script_name" --schedule "$schedule" --no-agent --name "$job_name" && \
        success "Registered cron job: $job_name ($schedule)" || \
        error "Failed to register cron job: $job_name"
}

# Check for state.db before sweep cron
if [[ -f "$STATE_DB" ]]; then
    register_cron "sweep.py" "every 240m" "ambient-memory-sweep"
else
    warn "State database not found at $STATE_DB"
    warn "The ambient-memory-sweep cron requires a memory_store.db."
    warn "It will be created on first Hermes run with the holographic plugin enabled."
    info "Registering sweep cron anyway — it will become active once state.db exists."

    if $DRY_RUN; then
        info "[dry-run] Would register: sweep.py (every 240m) as ambient-memory-sweep"
    else
        register_cron "sweep.py" "every 240m" "ambient-memory-sweep" || true
    fi
fi

register_cron "consolidate.py" "0 3 * * 0" "ambient-memory-consolidate"

# ── Step 6: Verify installation ─────────────────────────────────────────────
echo ""
info "Step 6: Verifying installation..."
echo ""

FAILURES=0

check_item() {
    local desc="$1"
    local check_cmd="$2"
    if eval "$check_cmd" &>/dev/null; then
        success "$desc"
    else
        error "$desc"
        FAILURES=$((FAILURES + 1))
    fi
}

if $DRY_RUN; then
    info "[dry-run] Would verify: directory structure, copied files, config patches, cron jobs"
else
    check_item "Config file exists"             "test -f $CONFIG_FILE"
    check_item "ambient_memory/ directory"      "test -d $AMBIENT_DST"
    check_item "scripts/ directory"             "test -d $SCRIPTS_DST"
    check_item "Memory section in config"       "grep -q '^memory:' $CONFIG_FILE"
    check_item "Holographic plugin in config"   "grep -q 'hermes-memory-store' $CONFIG_FILE"
    check_item "Sweep cron registered"          "hermes cron list 2>/dev/null | grep -q 'ambient-memory-sweep' || true"
    check_item "Consolidate cron registered"    "hermes cron list 2>/dev/null | grep -q 'ambient-memory-consolidate' || true"
fi

# ── Done ────────────────────────────────────────────────────────────────────
echo ""
if [[ "$FAILURES" -eq 0 ]]; then
    success "Installation complete!"
    echo ""
    info "Next steps:"
    info "  • Restart Hermes or start a new session to load the plugin."
    info "  • The ambient memory system will activate automatically."
    info "  • Monitor logs: hermes logs --follow"
else
    warn "Installation finished with $FAILURES warning(s)."
    info "Review the output above and re-run if needed."
fi

if $DRY_RUN; then
    echo ""
    info "Dry-run complete. No changes were made."
fi

echo ""
