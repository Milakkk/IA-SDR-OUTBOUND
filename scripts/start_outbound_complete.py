"""
Script completo para iniciar sistema outbound com tracking em CSV
- Inicia o bot WhatsApp (index.js)
- Inicia o serviço AI
- Envia mensagem inicial para contato específico
- Atualiza CSV periodicamente com todos os dados
"""
import subprocess
import time
import sys
import os
from pathlib import Path
import signal
import json
import requests
from datetime import datetime

# Caminhos
PROJECT_ROOT = Path(__file__).parent.parent
BOT_SCRIPT = PROJECT_ROOT / 'whatsapp-bot' / 'index.js'
AI_SERVICE_SCRIPT = PROJECT_ROOT / 'ai-service' / 'main.py'
CSV_TRACKER_SCRIPT = PROJECT_ROOT / 'scripts' / 'track_outbound_to_csv.py'
FOLLOW_UPS_FILE = PROJECT_ROOT / 'outbound_data' / 'follow_ups.json'

# Processos
bot_process = None
ai_process = None

# Configuração do contato
TARGET_PHONE = os.getenv('TARGET_PHONE', '5511999999999')
TARGET_EMPRESA = os.getenv('TARGET_EMPRESA', 'Empresa Exemplo')
TARGET_CONTATO = os.getenv('TARGET_CONTATO', 'Contato Exemplo')
TARGET_CARGO = os.getenv('TARGET_CARGO', '')

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
            [sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8001'],
            cwd=str(PROJECT_ROOT / 'ai-service'),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print(f'   ✅ Serviço AI iniciado (PID: {process.pid})')
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
        print(f'   ✅ Bot WhatsApp iniciado (PID: {process.pid})')
        return process
    except Exception as e:
        print(f'   ❌ Erro ao iniciar bot: {e}')
        return None

def send_initial_message():
    """Envia mensagem inicial via API do bot ou diretamente"""
    print(f'\n📤 Preparando mensagem inicial para {TARGET_EMPRESA} ({TARGET_PHONE})...')
    
    # Cria diretório se não existir
    FOLLOW_UPS_FILE.parent.mkdir(exist_ok=True)
    
    # Carrega follow-ups existentes
    follow_ups = {}
    if FOLLOW_UPS_FILE.exists():
        try:
            with open(FOLLOW_UPS_FILE, 'r', encoding='utf-8') as f:
                follow_ups = json.load(f)
        except Exception:
            pass
    
    # Verifica se já existe
    if TARGET_PHONE in follow_ups:
        print(f'   ⚠️  Contato já existe no sistema. Atualizando...')
    else:
        print(f'   ✅ Criando novo registro de follow-up...')
    
    # Agenda follow-ups
    now = datetime.now()
    follow_up_1_date = datetime.fromtimestamp(now.timestamp() + 2 * 24 * 60 * 60)  # +2 dias
    follow_up_2_date = datetime.fromtimestamp(follow_up_1_date.timestamp() + 7 * 24 * 60 * 60)  # +7 dias após
    
    follow_ups[TARGET_PHONE] = {
        'empresa': TARGET_EMPRESA,
        'contato': TARGET_CONTATO,
        'cargo': TARGET_CARGO,
        'sent_at': now.isoformat(),
        'responded': False,
        'follow_ups': [
            {
                'scheduled_for': follow_up_1_date.isoformat(),
                'sent': False,
                'message_type': 'follow_up_1'
            },
            {
                'scheduled_for': follow_up_2_date.isoformat(),
                'sent': False,
                'message_type': 'follow_up_2'
            }
        ],
        'status': 'active'
    }
    
    # Salva follow-ups
    with open(FOLLOW_UPS_FILE, 'w', encoding='utf-8') as f:
        json.dump(follow_ups, f, indent=2, ensure_ascii=False)
    
    print(f'   ✅ Follow-ups agendados:')
    print(f'      - Follow-up 1: {follow_up_1_date.strftime("%d/%m/%Y %H:%M")}')
    print(f'      - Follow-up 2: {follow_up_2_date.strftime("%d/%m/%Y %H:%M")}')
    print(f'\n   💡 A mensagem será enviada quando o bot WhatsApp estiver pronto.')
    print(f'   💡 Você pode enviar manualmente ou aguardar o bot conectar.')

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
            if result.stdout:
                print(f'   {result.stdout.strip()}')
        else:
            print(f'   ⚠️  Erro ao atualizar CSV: {result.stderr}')
    except Exception as e:
        print(f'   ⚠️  Erro ao atualizar CSV: {e}')

def wait_for_ai_service(max_wait=30):
    """Aguarda o serviço AI estar pronto"""
    print('⏳ Aguardando serviço AI inicializar...')
    for i in range(max_wait):
        try:
            response = requests.get('http://127.0.0.1:8001/docs', timeout=2)
            if response.status_code == 200:
                print('   ✅ Serviço AI está pronto!')
                return True
        except:
            pass
        time.sleep(1)
        if i % 5 == 0:
            print(f'   ⏳ Aguardando... ({i}/{max_wait}s)')
    print('   ⚠️  Serviço AI pode não estar pronto, mas continuando...')
    return False

def main():
    """Função principal"""
    print('=' * 80)
    print('🚀 SISTEMA OUTBOUND COMPLETO COM TRACKING CSV')
    print('=' * 80)
    print()
    
    print(f'📱 Contato alvo: {TARGET_EMPRESA} ({TARGET_PHONE})')
    print(f'👤 Contato: {TARGET_CONTATO}')
    print()
    
    # Prepara follow-ups
    send_initial_message()
    print()
    
    # Inicia serviços
    global ai_process, bot_process
    
    ai_process = start_ai_service()
    if not ai_process:
        print('❌ Não foi possível iniciar o serviço AI')
        return
    
    # Aguarda serviço AI estar pronto
    wait_for_ai_service()
    
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
    print('⚠️  IMPORTANTE:')
    print('   1. Escaneie o QR Code quando aparecer no terminal do bot')
    print('   2. Após conectar, você pode enviar a mensagem manualmente ou')
    print('      usar o script send_test_outbound.js para enviar')
    print('   3. O sistema detectará automaticamente respostas e atualizará o CSV')
    print()
    print('⚠️  Pressione Ctrl+C para encerrar')
    print()
    
    # Atualiza CSV imediatamente
    print(f'\n📊 [{datetime.now().strftime("%H:%M:%S")}] Atualizando CSV inicial...')
    update_csv()
    
    # Loop de atualização do CSV
    last_csv_update = time.time()
    csv_interval = 30  # Atualiza a cada 30 segundos
    
    try:
        while True:
            # Verifica se os processos ainda estão rodando
            if ai_process and ai_process.poll() is not None:
                print('⚠️  Serviço AI parou! Reiniciando...')
                ai_process = start_ai_service()
                wait_for_ai_service()
            
            if bot_process and bot_process.poll() is not None:
                print('⚠️  Bot WhatsApp parou!')
                # Não reinicia automaticamente (pode precisar de QR code)
            
            # Atualiza CSV periodicamente
            current_time = time.time()
            if current_time - last_csv_update >= csv_interval:
                print(f'\n📊 [{datetime.now().strftime("%H:%M:%S")}] Atualizando CSV...')
                update_csv()
                last_csv_update = current_time
            
            time.sleep(5)  # Verifica a cada 5 segundos
            
    except KeyboardInterrupt:
        signal_handler(None, None)

if __name__ == '__main__':
    main()
