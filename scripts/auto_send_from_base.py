"""
Script que monitora o CSV base e envia mensagens automaticamente
Integra monitor_contatos_base.py com send_from_queue.js
"""
import subprocess
import time
import sys
from pathlib import Path
import signal

PROJECT_ROOT = Path(__file__).parent.parent
MONITOR_SCRIPT = PROJECT_ROOT / 'scripts' / 'monitor_contatos_base.py'
SEND_SCRIPT = PROJECT_ROOT / 'whatsapp-bot' / 'send_from_queue.js'

monitor_process = None
send_process = None

def signal_handler(sig, frame):
    """Handler para Ctrl+C"""
    print('\n\n⚠️  Encerrando processos...')
    if monitor_process:
        monitor_process.terminate()
    if send_process:
        send_process.terminate()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def start_monitor():
    """Inicia o monitor de contatos"""
    print('🚀 Iniciando monitor de contatos base...')
    try:
        process = subprocess.Popen(
            [sys.executable, str(MONITOR_SCRIPT)],
            cwd=str(PROJECT_ROOT)
        )
        print(f'   ✅ Monitor iniciado (PID: {process.pid})')
        return process
    except Exception as e:
        print(f'   ❌ Erro: {e}')
        return None

def send_messages():
    """Envia mensagens da fila"""
    print('📤 Verificando fila de envio...')
    try:
        result = subprocess.run(
            ['node', str(SEND_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(result.stdout)
        else:
            if 'QR' in result.stdout or 'QR' in result.stderr:
                print('   ⚠️  Precisa escanear QR Code primeiro')
            else:
                print(f'   ⚠️  {result.stderr[:200]}')
    except Exception as e:
        print(f'   ⚠️  Erro: {e}')

def main():
    """Função principal"""
    print('=' * 80)
    print('🤖 SISTEMA AUTOMÁTICO DE ENVIO - CSV BASE')
    print('=' * 80)
    print()
    print('Este script:')
    print('  1. Monitora outbound_data/contatos_base.csv')
    print('  2. Quando você adicionar um contato, detecta automaticamente')
    print('  3. Envia mensagem via WhatsApp')
    print('  4. Atualiza CSV de tracking')
    print()
    print('📁 CSV Base: outbound_data/contatos_base.csv')
    print()
    print('⚠️  IMPORTANTE:')
    print('   - Certifique-se de que o bot WhatsApp está conectado')
    print('   - Execute: node whatsapp-bot/index.js primeiro')
    print('   - Ou escaneie o QR Code quando aparecer')
    print()
    print('⚠️  Pressione Ctrl+C para encerrar')
    print()
    
    global monitor_process
    monitor_process = start_monitor()
    
    if not monitor_process:
        print('❌ Não foi possível iniciar o monitor')
        return
    
    # Aguarda um pouco para o monitor iniciar
    time.sleep(2)
    
    # Loop: verifica fila e envia mensagens periodicamente
    last_send_check = time.time()
    send_interval = 30  # Verifica fila a cada 30 segundos
    
    try:
        while True:
            # Verifica se monitor ainda está rodando
            if monitor_process.poll() is not None:
                print('⚠️  Monitor parou! Reiniciando...')
                monitor_process = start_monitor()
                time.sleep(2)
            
            # Verifica fila e envia mensagens
            current_time = time.time()
            if current_time - last_send_check >= send_interval:
                send_messages()
                last_send_check = current_time
            
            time.sleep(10)  # Verifica a cada 10 segundos
            
    except KeyboardInterrupt:
        signal_handler(None, None)

if __name__ == '__main__':
    main()
