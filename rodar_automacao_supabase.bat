@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=python"
if exist "..\.venv\Scripts\python.exe" set "PYTHON_EXE=..\.venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

"%PYTHON_EXE%" "%~dp0automacao_supabase_leads.py" %*
endlocal
