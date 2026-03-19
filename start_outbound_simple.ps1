# Script PowerShell para iniciar sistema outbound completo
# Envia mensagem para contato específico e inicia tracking CSV

param(
    [string]$Phone = "5511999999999",
    [string]$Empresa = "Empresa",
    [string]$Contato = "Contato",
    [string]$Cargo = ""
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  SISTEMA OUTBOUND COM TRACKING CSV" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Define variáveis de ambiente
$env:TARGET_PHONE = $Phone
$env:TARGET_EMPRESA = $Empresa
$env:TARGET_CONTATO = $Contato
$env:TARGET_CARGO = $Cargo

Write-Host "📱 Contato alvo:" -ForegroundColor Yellow
Write-Host "   Telefone: $Phone" -ForegroundColor White
Write-Host "   Empresa: $Empresa" -ForegroundColor White
Write-Host "   Contato: $Contato" -ForegroundColor White
Write-Host ""

# Inicia serviço AI em background
Write-Host "🚀 Iniciando serviço AI..." -ForegroundColor Green
$aiJob = Start-Job -ScriptBlock {
    Set-Location $using:PWD
    python ai-service/main.py
}

Start-Sleep -Seconds 3

# Envia mensagem inicial
Write-Host "📤 Enviando mensagem inicial..." -ForegroundColor Green
node whatsapp-bot/send_single_outbound.js

Write-Host ""
Write-Host "✅ Sistema iniciado!" -ForegroundColor Green
Write-Host ""
Write-Host "📊 O CSV será atualizado automaticamente" -ForegroundColor Cyan
Write-Host "📁 Arquivo: outbound_data/outbound_tracking.csv" -ForegroundColor Cyan
Write-Host ""
Write-Host "⚠️  Para atualizar CSV manualmente, execute:" -ForegroundColor Yellow
Write-Host "   python scripts/track_outbound_to_csv.py" -ForegroundColor White
Write-Host ""
Write-Host "⚠️  Para iniciar o bot principal (receber respostas):" -ForegroundColor Yellow
Write-Host "   node whatsapp-bot/index.js" -ForegroundColor White
Write-Host ""

# Aguarda e atualiza CSV periodicamente
$lastUpdate = Get-Date
while ($true) {
    Start-Sleep -Seconds 30
    
    $now = Get-Date
    if (($now - $lastUpdate).TotalSeconds -ge 30) {
        Write-Host "[$($now.ToString('HH:mm:ss'))] Atualizando CSV..." -ForegroundColor Gray
        python scripts/track_outbound_to_csv.py 2>$null
        $lastUpdate = $now
    }
}
