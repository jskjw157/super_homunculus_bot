#!/bin/bash
# ── Super Homunculus Bot Auto-Executor (macOS/Linux) ──
# Called by launchd (macOS) or cron (Linux) every 60 seconds.
#
# 5-stage pipeline:
#   Stage 1: Find Claude CLI
#   Stage 2: Duplicate process detection + zombie kill
#   Stage 3: Lock recovery
#   Stage 4: Quick message check
#   Stage 5: Lock + Claude Code execution

# ─── Variables ──────────────────────────────────────────
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPF="$ROOT/CLAUDE.md"
LOG_DIR="$ROOT/logs"
LOG="$LOG_DIR/autoexecutor.log"
LOCKDIR="$ROOT/autoexecutor.lockdir"

mkdir -p "$LOG_DIR"

# Cleanup lock on exit (only if we own it)
LOCK_OWNER=false
cleanup() {
    if [ "$LOCK_OWNER" = true ]; then
        rm -rf "$LOCKDIR"
    fi
}
trap cleanup EXIT INT TERM

# ─── Prompt ─────────────────────────────────────────────
PROMPT="Check and process pending messages. Use the homunculus package: 1) adapter.fetch_pending() to get pending messages, 2) engine.merge_pending() to combine them, 3) send acknowledgement via adapter.send_text(), 4) engine.begin_work() to acquire lock and reserve workspace, 5) execute the user's task with progress updates, 6) adapter.deliver_result() to send output, 7) adapter.mark_completed() to mark messages done, 8) engine.finish_work() to update index and release lock. After completing, wait 3 minutes then check again. Repeat until no more messages, then exit."

# ═════════════════════════════════════════════════════════
# Stage 1: Find Claude CLI
# ═════════════════════════════════════════════════════════
CLAUDE_EXE=$(which claude 2>/dev/null)

if [ -z "$CLAUDE_EXE" ]; then
    for CANDIDATE in "/opt/homebrew/bin/claude" "/usr/local/bin/claude" "$HOME/.local/bin/claude"; do
        if [ -x "$CANDIDATE" ]; then
            CLAUDE_EXE="$CANDIDATE"
            break
        fi
    done
fi

if [ -z "$CLAUDE_EXE" ]; then
    echo "[ERROR] Claude CLI not found." >> "$LOG"
    exit 99
fi

# ═════════════════════════════════════════════════════════
# Stage 2: Duplicate process detection + zombie kill
# ═════════════════════════════════════════════════════════
PROCESS_PATTERN="claude.*append-system-prompt-file"
EXISTING_PID=$(pgrep -f "$PROCESS_PATTERN" 2>/dev/null)

if [ -n "$EXISTING_PID" ]; then
    STALE_LOG=$(find "$LOG" -mmin +10 2>/dev/null)

    if [ -z "$STALE_LOG" ]; then
        echo "[BLOCKED] Claude process running (PID: $EXISTING_PID)." >> "$LOG"
        exit 98
    else
        echo "[STALE] Zombie process detected (PID: $EXISTING_PID). Killing..." >> "$LOG"
        pkill -f "$PROCESS_PATTERN" 2>/dev/null
        sleep 2
        pkill -9 -f "$PROCESS_PATTERN" 2>/dev/null
        [ -d "$LOCKDIR" ] && rm -rf "$LOCKDIR"
        echo "[STALE] Cleanup done. Proceeding with new session." >> "$LOG"
    fi
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$LOG"

# ═════════════════════════════════════════════════════════
# Stage 3: Lock recovery (crash recovery)
# ═════════════════════════════════════════════════════════
if [ -d "$LOCKDIR" ]; then
    echo "[RECOVERY] Stale lock found (no process). Removing." >> "$LOG"
    rm -rf "$LOCKDIR"
fi

# Atomic lock acquisition (mkdir is POSIX atomic)
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    echo "[BLOCKED] Another process holds the lock." >> "$LOG"
    exit 98
fi
echo "$(date '+%Y-%m-%d %H:%M:%S') - PID: $$" > "$LOCKDIR/info"
LOCK_OWNER=true

# ═════════════════════════════════════════════════════════
# Stage 4: Quick message check (fast path)
# ═════════════════════════════════════════════════════════
echo "[CHECK] Looking for new messages..." >> "$LOG"
python3 "$ROOT/scripts/run_telegram.py" --check-only >> "$LOG" 2>&1
CHECK_EXIT=$?

case $CHECK_EXIT in
    0)
        echo "[CHECK] No new messages." >> "$LOG"
        exit 0
        ;;
    1)
        echo "[WORK] New messages found. Starting Claude Code..." >> "$LOG"
        ;;
    2)
        echo "[CHECK] Another task in progress (lock held)." >> "$LOG"
        exit 0
        ;;
    *)
        echo "[ERROR] Unexpected exit code: $CHECK_EXIT" >> "$LOG"
        exit 1
        ;;
esac

# ═════════════════════════════════════════════════════════
# Stage 5: Claude Code execution
# ═════════════════════════════════════════════════════════

if [ ! -f "$SPF" ]; then
    echo "[ERROR] CLAUDE.md not found: $SPF" >> "$LOG"
    exit 1
fi

export DISABLE_AUTOUPDATER=1
cd "$ROOT" || { echo "[ERROR] Cannot cd to $ROOT" >> "$LOG"; exit 2; }

# Try to resume existing session first
echo "[INFO] Attempting session resume..." >> "$LOG"
"$CLAUDE_EXE" -p -c --dangerously-skip-permissions \
    --append-system-prompt-file "$SPF" \
    "$PROMPT" >> "$LOG" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "[INFO] Resume failed (exit: $EXIT_CODE). Starting new session..." >> "$LOG"
    "$CLAUDE_EXE" -p --dangerously-skip-permissions \
        --append-system-prompt-file "$SPF" \
        "$PROMPT" >> "$LOG" 2>&1
    EXIT_CODE=$?
fi

# Lock cleanup handled by trap
echo "[INFO] Claude Code finished (exit: $EXIT_CODE)." >> "$LOG"
exit $EXIT_CODE
