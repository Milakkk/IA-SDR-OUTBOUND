@echo off
echo ========================================
echo   Verificando e enviando follow-ups
echo ========================================
echo.

cd /d %~dp0\..

echo Executando verificacao de follow-ups...
node whatsapp-bot\check_follow_ups.js

echo.
echo Verificando leads perdidos para criar no Salesforce...
python scripts\create_lost_leads.py

echo.
pause


