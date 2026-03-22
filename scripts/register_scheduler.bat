@echo off
chcp 65001 >NUL
setlocal EnableExtensions

echo ========================================
echo  Windows Task Scheduler Registration
echo ========================================
echo.

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."
pushd "%PROJECT_DIR%"
set "PROJECT_DIR=%CD%"
popd

set "BAT_FILE=%PROJECT_DIR%\scripts\autoexecutor.bat"

REM Dynamic task name from folder
for %%I in ("%PROJECT_DIR%") do set "FOLDER_NAME=%%~nxI"
set "TASK_NAME=Homunculus_%FOLDER_NAME%"

echo Project:   %PROJECT_DIR%
echo Task name: %TASK_NAME%
echo Executor:  %BAT_FILE%
echo.

REM Check admin
net session >NUL 2>&1
if errorlevel 1 (
    echo [ERROR] Administrator privileges required!
    echo         Right-click this file and select "Run as administrator"
    pause
    exit /b 1
)

echo Removing existing task (if any)...
schtasks /Delete /TN "%TASK_NAME%" /F >NUL 2>&1

echo Registering task (every 1 minute, hidden)...
schtasks /Create /TN "%TASK_NAME%" /TR "wscript.exe \"%PROJECT_DIR%\scripts\run_hidden.vbs\" \"%BAT_FILE%\"" /SC MINUTE /MO 1 /F
if errorlevel 1 (
    echo [ERROR] Registration failed!
    pause
    exit /b 1
)

echo.
echo ========================================
echo  Task registered successfully!
echo ========================================
echo.
echo Task name: %TASK_NAME%
echo Interval:  Every 1 minute
echo.
echo Commands:
echo   Check:   schtasks /Query /TN "%TASK_NAME%" /FO LIST
echo   Disable: schtasks /Change /TN "%TASK_NAME%" /DISABLE
echo   Enable:  schtasks /Change /TN "%TASK_NAME%" /ENABLE
echo   Delete:  schtasks /Delete /TN "%TASK_NAME%" /F
echo.
pause
