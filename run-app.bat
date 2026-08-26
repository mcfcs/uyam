@echo off
rem Start the Uyam Streamlit app (collection + annotation pipeline UI).
rem Double-click this file or run it from a terminal. Extra args are passed
rem through to streamlit, e.g.:  run-app.bat --server.port 8502
setlocal
title Uyam - Streamlit
cd /d "%~dp0"

rem Prefer the project venv when present, otherwise use the system Python.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

rem Reddit text is full of emoji; keep console output UTF-8 safe.
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"

"%PY%" -m streamlit run src\uyam\app.py %*

if errorlevel 1 (
    echo.
    echo Streamlit exited with an error. Is it installed? Try: pip install -e .
    pause
)
endlocal
