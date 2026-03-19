# Script para iniciar o SDR Outbound (Bot WhatsApp para Receita Federal)
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Iniciando SDR Outbound - Receita Federal" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Verifica se os serviços já estão rodando
$porta8001 = Get-NetTCPConnection -LocalPort 8001 -ErrorAction SilentlyContinue
if ($porta8001) {
    Write-Host "⚠️  Servico IA ja esta rodando na porta 8001" -ForegroundColor Yellow
} else {
    Write-Host "🚀 Iniciando servico de IA (FastAPI) na porta 8001..." -ForegroundColor Green
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot\ai-service'; python -m uvicorn main:app --host 127.0.0.1 --port 8001" -WindowStyle Minimized
    Start-Sleep -Seconds 3
}

# Verifica se o bot do WhatsApp já está rodando
$nodeProcess = Get-Process node -ErrorAction SilentlyContinue
if ($nodeProcess) {
    Write-Host "⚠️  Bot WhatsApp (Node.js) ja esta rodando" -ForegroundColor Yellow
} else {
    Write-Host "🚀 Iniciando bot do WhatsApp..." -ForegroundColor Green
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot\whatsapp-bot'; node index.js" -WindowStyle Normal
    Start-Sleep -Seconds 2
}

Write-Host ""
Write-Host "⏳ Aguardando servicos iniciarem..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# Verifica status
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Status dos Servicos" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Verifica servico IA
try {
    $response = Invoke-RestMethod -Uri "http://127.0.0.1:8001/" -TimeoutSec 3 -ErrorAction Stop
    Write-Host "✅ Servico IA: RODANDO (porta 8001)" -ForegroundColor Green
    Write-Host "   Status: $($response.status)" -ForegroundColor Gray
} catch {
    Write-Host "❌ Servico IA: NAO RESPONDE" -ForegroundColor Red
    Write-Host "   Erro: $_" -ForegroundColor Gray
}

# Verifica bot WhatsApp
$nodeProcess = Get-Process node -ErrorAction SilentlyContinue
if ($nodeProcess) {
    Write-Host "✅ Bot WhatsApp: RODANDO (Node.js PID: $($nodeProcess.Id))" -ForegroundColor Green
} else {
    Write-Host "❌ Bot WhatsApp: NAO ESTA RODANDO" -ForegroundColor Red
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Instrucoes" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "1. Verifique a janela do bot WhatsApp para escanear o QR code" -ForegroundColor White
Write-Host "2. Apos escanear, o bot estara pronto para receber respostas" -ForegroundColor White
Write-Host "3. Para enviar mensagens outbound:" -ForegroundColor White
Write-Host "   node whatsapp-bot\send_outbound_broadcast.js" -ForegroundColor Gray
Write-Host "4. Para processar CSVs da Receita:" -ForegroundColor White
Write-Host "   python scripts\process_csv_outbound.py" -ForegroundColor Gray
Write-Host "5. Para parar os servicos, feche as janelas do PowerShell" -ForegroundColor White
Write-Host ""



