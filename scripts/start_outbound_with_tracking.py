"""
Script principal para iniciar sistema outbound com tracking em CSV
- Inicia o bot WhatsApp
- Inicia o serviço AI
- Envia mensagem inicial para contato específico
- Atualiza CSV periodicamente
"""
import subprocess
import time
import sys
import os
from pathlib import Path
import signal

# Caminhos
PROJECT_ROOT = Path(__file__).parent.parent
BOT_SCRIPT = PROJECT_ROOT / 'whatsapp-bot' / 'send_single_outbound.js'
AI_SERVICE_SCRIPT = PROJECT_ROOT / 'ai-service' / 'main.py'
CSV_TRACKER_SCRIPT = PROJECT_ROOT / 'scripts' / 'track_outbound_to_csv.py'

# Processos
bot_process = None
ai_process = None

def signal_handler(sig, frame):
    """Handler para Ctrl+C"""
    print('\n\n⚠️  Encerrando processos...')
    if bot_process:
        bot_process.terminate()
    if ai_process:
        ai_process.terminate()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def start_ai_service():
    """Inicia o serviço AI"""
    print('🚀 Iniciando serviço AI...')
    try:
        process = subprocess.Popen(
            [sys.executable, str(AI_SERVICE_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print('   ✅ Serviço AI iniciado (PID: {})'.format(process.pid))
        return process
    except Exception as e:
        print(f'   ❌ Erro ao iniciar serviço AI: {e}')
        return None

def start_bot():
    """Inicia o bot WhatsApp"""
    print('🚀 Iniciando bot WhatsApp...')
    try:
        # Verifica se Node.js está disponível
        node_check = subprocess.run(['node', '--version'], capture_output=True)
        if node_check.returncode != 0:
            print('   ❌ Node.js não encontrado!')
            return None
        
        process = subprocess.Popen(
            ['node', str(BOT_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print('   ✅ Bot WhatsApp iniciado (PID: {})'.format(process.pid))
        return process
    except Exception as e:
        print(f'   ❌ Erro ao iniciar bot: {e}')
        return None

def update_csv():
    """Atualiza o CSV de tracking"""
    try:
        result = subprocess.run(
            [sys.executable, str(CSV_TRACKER_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print('   ✅ CSV atualizado')
        else:
            print(f'   ⚠️  Erro ao atualizar CSV: {result.stderr}')
    except Exception as e:
        print(f'   ⚠️  Erro ao atualizar CSV: {e}')

def main():
    """Função principal"""
    print('=' * 80)
    print('🚀 SISTEMA OUTBOUND COM TRACKING CSV')
    print('=' * 80)
    print()
    
    # Verifica se o número foi especificado
    target_phone = os.getenv('TARGET_PHONE', '5511999999999')
    target_empresa = os.getenv('TARGET_EMPRESA', 'Empresa Exemplo')
    target_contato = os.getenv('TARGET_CONTATO', 'Contato Exemplo')
    
    print(f'📱 Contato alvo: {target_empresa} ({target_phone})')
    print(f'👤 Contato: {target_contato}')
    print()
    
    # Inicia serviços
    global ai_process, bot_process
    
    ai_process = start_ai_service()
    if not ai_process:
        print('❌ Não foi possível iniciar o serviço AI')
        return
    
    # Aguarda um pouco para o serviço AI iniciar
    print('⏳ Aguardando serviço AI inicializar...')
    time.sleep(3)
    
    bot_process = start_bot()
    if not bot_process:
        print('❌ Não foi possível iniciar o bot')
        if ai_process:
            ai_process.terminate()
        return
    
    print()
    print('=' * 80)
    print('✅ Sistema iniciado!')
    print('=' * 80)
    print()
    print('📊 O CSV será atualizado automaticamente a cada 30 segundos')
    print('📁 Arquivo CSV: outbound_data/outbound_tracking.csv')
    print()
    print('⚠️  Pressione Ctrl+C para encerrar')
    print()
    
    # Loop de atualização do CSV
    last_csv_update = 0
    csv_interval = 30  # Atualiza a cada 30 segundos
    
    try:
        while True:
            # Verifica se os processos ainda estão rodando
            if ai_process and ai_process.poll() is not None:
                print('⚠️  Serviço AI parou!')
                ai_process = start_ai_service()
            
            if bot_process and bot_process.poll() is not None:
                print('⚠️  Bot WhatsApp parou!')
                # Não reinicia o bot automaticamente (pode precisar de QR code)
            
            # Atualiza CSV periodicamente
            current_time = time.time()
            if current_time - last_csv_update >= csv_interval:
                print(f'\n📊 [{time.strftime("%H:%M:%S")}] Atualizando CSV...')
                update_csv()
                last_csv_update = current_time
            
            time.sleep(5)  # Verifica a cada 5 segundos
            
    except KeyboardInterrupt:
        signal_handler(None, None)

if __name__ == '__main__':
    main()
