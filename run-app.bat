@echo off
rem Start the Uyam Streamlit app (collection + annotation pipeline UI).
rem Binds to this machine's Tailscale IP when Tailscale is running, so the app
rem is reachable from any device on your tailnet at http://<tailscale-ip>:8501
rem (falls back to localhost-only when Tailscale is unavailable).
rem Extra args are passed through to streamlit, e.g.:  run-app.bat --server.port 8502
rem Port 8501 is dedicated to this app: any process already listening there is ended.
setlocal EnableDelayedExpansion
title Uyam - Streamlit
cd /d "%~dp0"

rem Prefer the project venv when present, otherwise use the system Python.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

rem Reddit text is full of emoji; keep console output UTF-8 safe.
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"

rem Default Streamlit port. Override with --server.port N or --server.port=N.
set "PORT=8501"
set "EXPECT_PORT="
for %%A in (%*) do (
    if defined EXPECT_PORT (
        set "PORT=%%~A"
        set "EXPECT_PORT="
    ) else (
        set "ARG=%%~A"
        if /I "!ARG!"=="--server.port" (
            set "EXPECT_PORT=1"
        ) else if /I "!ARG:~0,14!"=="--server.port=" (
            set "PORT=!ARG:~14!"
        )
    )
)

rem End whatever is already LISTENING on this port so Uyam always owns it.
rem No /T: the scraper and annotation jobs Streamlit launched are detached
rem children that must survive an app restart (taskkill /T would end them).
echo Checking port %PORT%...
set "KILLED="
for /f "tokens=5" %%P in ('netstat -ano 2^>nul ^| findstr /C:":%PORT% " ^| findstr /C:"LISTENING"') do (
    if not "%%P"=="0" if not "%%P"=="" (
        echo Port %PORT% in use by PID %%P - ending it so Uyam can bind.
        taskkill /F /PID %%P >nul 2>&1
        set "KILLED=1"
    )
)
if defined KILLED (
    rem Give Windows a moment to release the socket.
    timeout /t 1 /nobreak >nul
)

rem Detect this machine's Tailscale IPv4 (first line of `tailscale ip -4`).
set "ADDR=localhost"
for /f "usebackq delims=" %%i in (`tailscale ip -4 2^>nul`) do (
    if not defined TSIP set "TSIP=%%i"
)
if defined TSIP (
    set "ADDR=%TSIP%"
    echo Tailscale detected - serving on http://%TSIP%:%PORT% ^(tailnet devices^) and this machine.
) else (
    echo Tailscale not detected - serving on http://localhost:%PORT% only.
)

"%PY%" -m streamlit run src\uyam\app.py --server.address %ADDR% --server.port %PORT% %*

if errorlevel 1 (
    echo.
    echo Streamlit exited with an error. Is it installed? Try: pip install -e .
    pause
)
endlocal
