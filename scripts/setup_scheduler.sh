#!/bin/bash
# Register the auto-executor with the OS scheduler.
# macOS: launchd plist  |  Linux: crontab
#
# Usage: bash scripts/setup_scheduler.sh
#
# For Windows, run scripts\register_scheduler.bat instead.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
EXECUTOR="$SCRIPT_DIR/autoexecutor.sh"

# Detect OS
case "$(uname -s)" in
    Darwin)  OS="macos" ;;
    Linux)   OS="linux" ;;
    *)       echo "Unsupported OS. Use register_scheduler.bat on Windows."; exit 1 ;;
esac

chmod +x "$EXECUTOR"

setup_macos() {
    local plist_name="com.homunculus.autoexecutor.plist"
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
        <string>/bin/bash</string>
        <string>${EXECUTOR}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>StartInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>${PROJECT_DIR}/logs/launchd_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${PROJECT_DIR}/logs/launchd_stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF

    mkdir -p "${PROJECT_DIR}/logs"
    launchctl unload "$plist_path" 2>/dev/null || true
    launchctl load "$plist_path"
    echo "Registered: $plist_name (every 60s)"
    echo ""
    echo "Commands:"
    echo "  Stop:    launchctl unload $plist_path"
    echo "  Restart: launchctl unload $plist_path && launchctl load $plist_path"
    echo "  Logs:    tail -f ${PROJECT_DIR}/logs/autoexecutor.log"
}

setup_linux() {
    local cmd="cd ${PROJECT_DIR} && bash scripts/autoexecutor.sh"

    mkdir -p "${PROJECT_DIR}/logs"
    (crontab -l 2>/dev/null | grep -v "autoexecutor.sh"; echo "* * * * * $cmd") | crontab -
    echo "Cron registered: every 1 minute"
    echo ""
    echo "Commands:"
    echo "  Check:  crontab -l | grep homunculus"
    echo "  Remove: crontab -l | grep -v autoexecutor.sh | crontab -"
    echo "  Logs:   tail -f ${PROJECT_DIR}/logs/autoexecutor.log"
}

echo "========================================="
echo " Super Homunculus Bot — Scheduler Setup"
echo "========================================="
echo "OS: $OS"
echo "Project: $PROJECT_DIR"
echo "Executor: $EXECUTOR"
echo ""

if [[ "$OS" == "macos" ]]; then
    setup_macos
elif [[ "$OS" == "linux" ]]; then
    setup_linux
fi

echo ""
echo "Done! The bot will check for messages every 60 seconds."
