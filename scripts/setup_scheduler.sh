#!/bin/bash
# Setup scheduled task execution (macOS launchd / Linux cron / Windows Task Scheduler)
#
# Usage: bash scripts/setup_scheduler.sh [telegram|discord|both]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLATFORM="${1:-both}"

# Detect OS
case "$(uname -s)" in
    Darwin)  OS="macos" ;;
    Linux)   OS="linux" ;;
    MINGW*|MSYS*|CYGWIN*) OS="windows" ;;
    *)       echo "Unsupported OS"; exit 1 ;;
esac

setup_macos() {
    local name="$1"
    local script="$2"
    local interval="$3"
    local plist_name="com.homunculus.${name}.plist"
    local plist_path="$HOME/Library/LaunchAgents/$plist_name"

    cat > "$plist_path" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$plist_name</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(which python3)</string>
        <string>${PROJECT_DIR}/scripts/${script}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>StartInterval</key>
    <integer>${interval}</integer>
    <key>StandardOutPath</key>
    <string>${PROJECT_DIR}/logs/${name}.log</string>
    <key>StandardErrorPath</key>
    <string>${PROJECT_DIR}/logs/${name}_error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF

    mkdir -p "${PROJECT_DIR}/logs"
    launchctl unload "$plist_path" 2>/dev/null || true
    launchctl load "$plist_path"
    echo "  Registered: $plist_name (every ${interval}s)"
}

setup_linux() {
    local script="$1"
    local interval="$2"
    local cron_expr="*/${interval} * * * *"
    local cmd="cd ${PROJECT_DIR} && $(which python3) scripts/${script} >> logs/${script%.py}.log 2>&1"

    mkdir -p "${PROJECT_DIR}/logs"
    (crontab -l 2>/dev/null | grep -v "$script"; echo "$cron_expr $cmd") | crontab -
    echo "  Cron added: $cron_expr $cmd"
}

echo "Setting up scheduler ($OS) for: $PLATFORM"
echo "Project: $PROJECT_DIR"
echo ""

if [[ "$PLATFORM" == "telegram" || "$PLATFORM" == "both" ]]; then
    echo "Telegram processor:"
    if [[ "$OS" == "macos" ]]; then
        setup_macos "telegram" "run_telegram.py" 30
    elif [[ "$OS" == "linux" ]]; then
        setup_linux "run_telegram.py" 1
    fi
fi

if [[ "$PLATFORM" == "discord" || "$PLATFORM" == "both" ]]; then
    echo "Discord processor:"
    if [[ "$OS" == "macos" ]]; then
        setup_macos "discord" "run_discord.py" 30
    elif [[ "$OS" == "linux" ]]; then
        setup_linux "run_discord.py" 1
    fi
fi

echo ""
echo "Done! Check logs/ for output."
