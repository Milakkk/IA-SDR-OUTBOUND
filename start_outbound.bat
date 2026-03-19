@echo off
echo ========================================
echo   Iniciando SDR Outbound - Receita Federal
echo ========================================
echo.

REM Inicia servico de IA
echo [1/2] Iniciando servico de IA (FastAPI)...
start "SDR Outbound - Servico IA" /MIN cmd /k "cd /d %~dp0ai-service && python -m uvicorn main:app --host 127.0.0.1 --port 8001"

timeout /t 3 /nobreak >nul

REM Inicia bot WhatsApp
echo [2/2] Iniciando bot do WhatsApp...
start "SDR Outbound - Bot WhatsApp" cmd /k "cd /d %~dp0whatsapp-bot && node index.js"

echo.
echo ========================================
echo   Servicos iniciados!
echo ========================================
echo.
echo 1. Verifique a janela "SDR Outbound - Bot WhatsApp" para escanear o QR code
echo 2. Apos escanear, o bot estara pronto para receber respostas
echo 3. Para enviar mensagens outbound, execute:
echo    node whatsapp-bot\send_outbound_broadcast.js
echo 4. Para processar CSVs, execute:
echo    python scripts\process_csv_outbound.py
echo 5. Para parar os servicos, feche as janelas abertas
echo.
pause



