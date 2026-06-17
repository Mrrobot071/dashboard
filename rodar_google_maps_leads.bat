@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=python"
set "PYTHON_ARGS="
if exist "..\.venv\Scripts\python.exe" set "PYTHON_EXE=..\.venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

echo %* | findstr /I /C:"--modo navegador" /C:"--modo=navegador" >nul
if not errorlevel 1 (
  "%PYTHON_EXE%" %PYTHON_ARGS% -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('playwright') else 1)" >nul 2>nul
  if errorlevel 1 (
    py -3.13 -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('playwright') else 1)" >nul 2>nul
    if not errorlevel 1 (
      set "PYTHON_EXE=py"
      set "PYTHON_ARGS=-3.13"
    )
  )
)

"%PYTHON_EXE%" %PYTHON_ARGS% "%~dp0coletar_google_maps_leads.py" %*
endlocal
