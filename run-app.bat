@echo off
rem Start the Uyam Streamlit app (collection + annotation pipeline UI).
rem Binds to this machine's Tailscale IP when Tailscale is running, so the app
rem is reachable from any device on your tailnet at http://<tailscale-ip>:8501
rem (falls back to localhost-only when Tailscale is unavailable).
rem Extra args are passed through to streamlit, e.g.:  run-app.bat --server.port 8502
setlocal
title Uyam - Streamlit
cd /d "%~dp0"

rem Prefer the project venv when present, otherwise use the system Python.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

rem Reddit text is full of emoji; keep console output UTF-8 safe.
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"

rem Detect this machine's Tailscale IPv4 (first line of `tailscale ip -4`).
set "ADDR=localhost"
for /f "usebackq delims=" %%i in (`tailscale ip -4 2^>nul`) do (
    if not defined TSIP set "TSIP=%%i"
)
if defined TSIP (
    set "ADDR=%TSIP%"
    echo Tailscale detected - serving on http://%TSIP%:8501 ^(tailnet devices^) and this machine.
) else (
    echo Tailscale not detected - serving on http://localhost:8501 only.
)

"%PY%" -m streamlit run src\uyam\app.py --server.address %ADDR% %*

if errorlevel 1 (
    echo.
    echo Streamlit exited with an error. Is it installed? Try: pip install -e .
    pause
)
endlocal
