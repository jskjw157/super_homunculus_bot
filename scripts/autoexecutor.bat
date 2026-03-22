@echo off
setlocal EnableExtensions

REM ── Super Homunculus Bot Auto-Executor ──
REM Scheduled by Windows Task Scheduler to run every 1 minute.
REM Checks for new messages and launches Claude Code if needed.

set "BASE=%~dp0.."
pushd "%BASE%" >NUL 2>&1
set "ROOT=%CD%"
popd >NUL 2>&1

set "LOG=%ROOT%\logs\autoexecutor.log"
set "LOCKFILE=%ROOT%\autoexecutor.lock"

REM Ensure logs directory exists
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"

REM ── Find Claude CLI ──
set "CLAUDE_EXE="

if exist "%USERPROFILE%\.local\bin\claude.exe" (
    set "CLAUDE_EXE=%USERPROFILE%\.local\bin\claude.exe"
    goto CLAUDE_FOUND
)
if exist "%APPDATA%\npm\claude.cmd" (
    set "CLAUDE_EXE=%APPDATA%\npm\claude.cmd"
    goto CLAUDE_FOUND
)
if exist "%USERPROFILE%\AppData\Local\Programs\claude\claude.exe" (
    set "CLAUDE_EXE=%USERPROFILE%\AppData\Local\Programs\claude\claude.exe"
    goto CLAUDE_FOUND
)

echo [ERROR] Claude CLI not found.>> "%LOG%"
exit /b 99

:CLAUDE_FOUND

echo ===== %date% %time% =====>> "%LOG%"

REM ── Duplicate process guard ──
tasklist /FI "IMAGENAME eq node.exe" 2>NUL | find /I "node.exe" >NUL
if errorlevel 1 goto NO_PROCESS
wmic process where "name='node.exe'" get commandline 2>NUL | find /I "claude" | find /I "append-system-prompt-file" >NUL
if errorlevel 1 goto NO_PROCESS

echo [BLOCKED] Claude already running.>> "%LOG%"
exit /b 98

:NO_PROCESS

REM ── Stale lock recovery ──
if not exist "%LOCKFILE%" goto LOCK_OK
echo [RECOVERY] Removing stale lock.>> "%LOG%"
del "%LOCKFILE%" 2>NUL
:LOCK_OK

REM ── Quick message check ──
pushd "%ROOT%" >NUL 2>&1
python scripts\run_telegram.py --check-only >> "%LOG%" 2>&1
set "CHECK_RESULT=%ERRORLEVEL%"
popd >NUL 2>&1

if %CHECK_RESULT% EQU 0 (
    echo [IDLE] No new messages.>> "%LOG%"
    exit /b 0
)

echo [WORK] New messages found. Starting Claude...>> "%LOG%"

REM ── Create lock ──
echo %date% %time%> "%LOCKFILE%"

pushd "%ROOT%" >NUL 2>&1

set DISABLE_AUTOUPDATER=1

call "%CLAUDE_EXE%" -p -c --dangerously-skip-permissions ^
  --append-system-prompt-file "%ROOT%\CLAUDE.md" ^
  "Check and process pending messages. Use the homunculus package: 1) TelegramAdapter.fetch_pending() 2) TaskEngine.merge_pending() 3) send acknowledgement 4) TaskEngine.begin_work() 5) execute task 6) deliver_result() 7) mark_completed() 8) finish_work(). After completing, wait 3 minutes and check again. Repeat until no more messages." ^
  >> "%LOG%" 2>&1

set "EC=%ERRORLEVEL%"

REM ── Cleanup ──
if exist "%LOCKFILE%" del "%LOCKFILE%" 2>NUL

popd
echo EXITCODE=%EC%>> "%LOG%"
exit /b %EC%
