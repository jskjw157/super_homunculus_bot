@echo off
chcp 65001 >NUL
setlocal EnableExtensions EnableDelayedExpansion

echo ========================================
echo  Super Homunculus Bot - Setup
echo ========================================
echo.

REM Auto-detect project directory
set "PROJECT_DIR=%~dp0.."
pushd "%PROJECT_DIR%"
set "PROJECT_DIR=%CD%"
popd

echo [1/4] Project path
echo       %PROJECT_DIR%
echo.

REM Check Python
echo [2/4] Checking Python...
python --version >NUL 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    echo         Install from https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version
echo.

REM Install packages
echo [3/4] Installing packages...
pip install -e "%PROJECT_DIR%"
if errorlevel 1 (
    echo [ERROR] Package installation failed!
    pause
    exit /b 1
)
echo       Done!
echo.

REM Check .env
echo [4/4] Checking .env...
if not exist "%PROJECT_DIR%\.env" (
    echo [SETUP] Creating .env file.
    echo.

    set /p BOT_TOKEN="Telegram bot token (from @BotFather): "
    set /p USER_ID="Your Telegram user ID: "

    (
        echo TELEGRAM_BOT_TOKEN=!BOT_TOKEN!
        echo TELEGRAM_ALLOWED_USERS=!USER_ID!
        echo TELEGRAM_POLLING_INTERVAL=10
    ) > "%PROJECT_DIR%\.env"

    echo       .env created!
) else (
    echo       .env exists
)
echo.

echo ========================================
echo  Setup complete!
echo ========================================
echo.
echo Next steps:
echo   1. Find your user ID:
echo      python scripts\get_my_id.py
echo.
echo   2. Register Windows Task Scheduler:
echo      scripts\register_scheduler.bat
echo.
echo   3. Manual test:
echo      python scripts\run_telegram.py
echo.
pause
