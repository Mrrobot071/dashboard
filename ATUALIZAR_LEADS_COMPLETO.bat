@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=python"
set "PYTHON_ARGS="

if exist "..\.venv\Scripts\python.exe" set "PYTHON_EXE=..\.venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

call :HAS_PLAYWRIGHT "%PYTHON_EXE%" %PYTHON_ARGS%
if errorlevel 1 (
  py -3.13 -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('playwright') else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_EXE=py"
    set "PYTHON_ARGS=-3.13"
  )
)

call :HAS_PLAYWRIGHT "%PYTHON_EXE%" %PYTHON_ARGS%
if errorlevel 1 (
  echo.
  echo Playwright nao foi encontrado no Python disponivel.
  echo Instale com:
  echo py -3.13 -m pip install playwright
  echo py -3.13 -m playwright install chromium
  echo.
  exit /b 1
)

echo.
echo ============================================================
echo  ATUALIZACAO AUTOMATICA DE LEADS
echo ============================================================
echo  - Busca novos leads no Google Maps
echo  - Confere duplicidade contra CSV, Supabase e pendencias
echo  - Tenta pegar pelo menos 10 novos por consulta
echo  - Salva cada lead novo direto no CSV principal
echo  - Insere cada lead novo direto no Supabase pela API
echo ============================================================
echo.

"%PYTHON_EXE%" %PYTHON_ARGS% "%~dp0coletar_google_maps_leads.py" ^
  --modo navegador ^
  --perfil rapido ^
  --no-usar-termos-csv ^
  --max-segundos 0 ^
  --parar-sem-novos 0 ^
  --parar-apos-erros 3 ^
  --max-resultados-consulta 80 ^
  --max-scrolls 18 ^
  --limite-total 0 ^
  --min-novos-por-consulta 10 ^
  --timeout 30 ^
  --atualizar-direto ^
  --processar-pendencias-direto ^
  %*

set "EXIT_CODE=%ERRORLEVEL%"
echo %* | findstr /I /C:"--dry-run" >nul
if errorlevel 1 (
  set "DRY_RUN=0"
) else (
  set "DRY_RUN=1"
)
echo.
if "%EXIT_CODE%"=="0" (
  if "%DRY_RUN%"=="1" (
    echo Simulacao concluida. Nada foi gravado nem sincronizado.
  ) else (
    echo Concluido. Leads novos foram gravados direto no CSV principal e enviados ao Supabase.
  )
) else (
  echo Falhou com codigo %EXIT_CODE%. Veja os logs na pasta logs.
)
echo.
exit /b %EXIT_CODE%

:HAS_PLAYWRIGHT
"%~1" %2 -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('playwright') else 1)" >nul 2>nul
exit /b %ERRORLEVEL%
