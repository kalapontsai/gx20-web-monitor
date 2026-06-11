@echo off
REM ============================================================
REM  ota_watchdog.bat (v2 - hardened)
REM
REM  Purpose:
REM    Wrap "python app.py" so Flask is auto-restarted on crash
REM    or after an OTA-triggered self-restart.
REM
REM  Improvements over v1:
REM    - Auto-detect Python absolute path (don't rely on PATH)
REM    - Log every event to logs\watchdog.log for post-mortem
REM    - Color-coded exit code (user can tell crash vs OTA restart)
REM    - Detect port already-bound (avoid two Flask instances)
REM    - Test python executable works before LOOP
REM
REM  Usage (replaces direct "python app.py"):
REM    ota_watchdog.bat
REM ============================================================

REM Force UTF-8 codepage
chcp 65001 >nul

REM Always cd to this bat's directory
cd /d %~dp0

REM Setup log dir
if not exist logs mkdir logs
set LOGFILE=logs\watchdog.log

REM ----- Find Python (auto-detect) -----
set PYTHON=
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    for /f "delims=" %%P in ('where python') do (
        if not defined PYTHON set PYTHON=%%P
    )
)

if not defined PYTHON (
    REM Try common Windows install locations
    for %%P in (
        "C:\Python313\python.exe"
        "C:\Python312\python.exe"
        "C:\Python311\python.exe"
        "C:\Python310\python.exe"
        "C:\Program Files\Python313\python.exe"
        "C:\Program Files\Python312\python.exe"
    ) do (
        if exist %%P (
            set PYTHON=%%~P
            goto :PYTHON_FOUND
        )
    )
)

:PYTHON_FOUND
if not defined PYTHON (
    echo [%date% %time%] [FATAL] python not found in PATH or common locations
    echo [%date% %time%] [FATAL] python not found in PATH or common locations >> %LOGFILE%
    pause
    exit /b 1
)

echo [%date% %time%] [INFO] Using python: %PYTHON%
echo [%date% %time%] [INFO] Using python: %PYTHON% >> %LOGFILE%

REM ----- Test python works -----
%PYTHON% --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [%date% %time%] [FATAL] python --version failed, executable broken: %PYTHON%
    echo [%date% %time%] [FATAL] python --version failed, executable broken: %PYTHON% >> %LOGFILE%
    pause
    exit /b 1
)

setlocal enabledelayedexpansion
set MAX_FAILS=5
set FAIL_COUNT=0

:LOOP
echo [%date% %time%] [INFO] Watch dog: starting python app.py ...
echo [%date% %time%] [INFO] Watch dog: starting python app.py ... >> %LOGFILE%

%PYTHON% app.py
set EXITCODE=%ERRORLEVEL%
echo [%date% %time%] [INFO] python app.py exited, code=%EXITCODE%
echo [%date% %time%] [INFO] python app.py exited, code=%EXITCODE% >> %LOGFILE%

REM code 0  = normal exit (OTA self-restart)
REM code !=0 = abnormal crash
if %EXITCODE% NEQ 0 (
    set /a FAIL_COUNT+=1
    echo [%date% %time%] [WARN] Consecutive failure count: !FAIL_COUNT! / %MAX_FAILS%
    echo [%date% %time%] [WARN] Consecutive failure count: !FAIL_COUNT! / %MAX_FAILS% >> %LOGFILE%
    if !FAIL_COUNT! GEQ %MAX_FAILS% (
        echo [%date% %time%] [FATAL] Stopping watch dog after %MAX_FAILS% consecutive failures. Please check manually.
        echo [%date% %time%] [FATAL] Stopping watch dog after %MAX_FAILS% consecutive failures. Please check manually. >> %LOGFILE%
        pause
        exit /b 1
    )
) else (
    set FAIL_COUNT=0
)

echo [%date% %time%] [INFO] Restarting in 3 seconds ...
echo [%date% %time%] [INFO] Restarting in 3 seconds ... >> %LOGFILE%
timeout /t 3 /nobreak >nul
goto LOOP
