@echo off
setlocal
cd /d "%~dp0"

call "%~dp0rodar_google_maps_leads.bat" --modo navegador --perfil rapido --no-usar-termos-csv --max-segundos 0 --parar-sem-novos 0 --parar-apos-erros 3 --max-resultados-consulta 80 --max-scrolls 18 --limite-total 0 --min-novos-por-consulta 10 --timeout 30 --atualizar-direto --processar-pendencias-direto %*
endlocal
