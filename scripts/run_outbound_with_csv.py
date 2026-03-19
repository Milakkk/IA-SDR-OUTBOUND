"""
Script principal para rodar sistema outbound completo com atualização automática de CSV
- Inicia o bot WhatsApp (index.js) em background
- Atualiza CSV periodicamente com todos os dados
- Monitora status das conversas
"""
import subprocess
import time
import sys
import os
from pathlib import Path
import signal
from datetime import datetime
import json

# Caminhos
PROJECT_ROOT = Path(__file__).parent.parent
CSV_TRACKER_SCRIPT = PROJECT_ROOT / 'scripts' / 'track_outbound_to_csv.py'
FOLLOW_UPS_FILE = PROJECT_ROOT / 'outbound_data' / 'follow_ups.json'

# Processos
bot_process = None

def signal_handler(sig, frame):
    """Handler para Ctrl+C"""
    print('\n\n⚠️  Encerrando...')
    if bot_process:
        bot_process.terminate()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def update_csv():
    """Atualiza o CSV de tracking"""
    try:
        result = subprocess.run(
            [sys.executable, str(CSV_TRACKER_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            if output:
                print(f'   {output}')
            return True
        else:
            print(f'   ⚠️  Erro: {result.stderr[:200]}')
            return False
    except Exception as e:
        print(f'   ⚠️  Erro: {str(e)[:200]}')
        return False

def check_follow_ups_status():
    """Verifica status dos follow-ups e atualiza responded se necessário"""
    if not FOLLOW_UPS_FILE.exists():
        return
    
    try:
        with open(FOLLOW_UPS_FILE, 'r', encoding='utf-8') as f:
            follow_ups = json.load(f)
        
        # Carrega estado das conversas para verificar respostas
        conversations_file = PROJECT_ROOT / 'conversations_state.json'
        if conversations_file.exists():
            with open(conversations_file, 'r', encoding='utf-8') as f:
                conversations = json.load(f)
            
            # Verifica se algum contato respondeu
            conversation_history = conversations.get('conversation_history', {})
            needs_update = False
            
            for phone, follow_up_data in follow_ups.items():
                if not follow_up_data.get('responded', False):
                    # Verifica se há histórico de conversa para este telefone
                    if phone in conversation_history:
                        messages = conversation_history[phone]
                        # Se há mensagens do usuário (role: 'user'), significa que respondeu
                        user_messages = [m for m in messages if m.get('role') == 'user']
                        if user_messages:
                            follow_up_data['responded'] = True
                            follow_up_data['responded_at'] = datetime.now().isoformat()
                            follow_up_data['status'] = 'responded'
                            needs_update = True
                            print(f'   ✅ {follow_up_data.get("empresa", phone)} respondeu!')
            
            if needs_update:
                with open(FOLLOW_UPS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(follow_ups, f, indent=2, ensure_ascii=False)
    
    except Exception as e:
        print(f'   ⚠️  Erro ao verificar follow-ups: {str(e)[:100]}')

def main():
    """Função principal"""
    print('=' * 80)
    print('📊 SISTEMA DE TRACKING CSV - OUTBOUND')
    print('=' * 80)
    print()
    print('Este script atualiza o CSV periodicamente com todos os dados de outbound.')
    print('Certifique-se de que:')
    print('  1. O serviço AI está rodando (uvicorn main:app --port 8001)')
    print('  2. O bot WhatsApp está rodando (node whatsapp-bot/index.js)')
    print()
    print('📁 Arquivo CSV: outbound_data/outbound_tracking.csv')
    print('📁 Arquivo Follow-ups: outbound_data/follow_ups.json')
    print()
    print('⚠️  Pressione Ctrl+C para encerrar')
    print()
    
    # Atualiza CSV imediatamente
    print(f'📊 [{datetime.now().strftime("%H:%M:%S")}] Atualizando CSV inicial...')
    update_csv()
    print()
    
    # Loop de atualização
    last_csv_update = time.time()
    last_followup_check = time.time()
    csv_interval = 30  # Atualiza CSV a cada 30 segundos
    followup_check_interval = 10  # Verifica follow-ups a cada 10 segundos
    
    try:
        while True:
            current_time = time.time()
            
            # Verifica status de follow-ups
            if current_time - last_followup_check >= followup_check_interval:
                check_follow_ups_status()
                last_followup_check = current_time
            
            # Atualiza CSV periodicamente
            if current_time - last_csv_update >= csv_interval:
                print(f'\n📊 [{datetime.now().strftime("%H:%M:%S")}] Atualizando CSV...')
                update_csv()
                last_csv_update = current_time
            
            time.sleep(5)  # Verifica a cada 5 segundos
            
    except KeyboardInterrupt:
        signal_handler(None, None)

if __name__ == '__main__':
    main()
