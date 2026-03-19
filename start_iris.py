"""
Versão simplificada - Inicia tudo automaticamente sem perguntas
"""
import subprocess
import time
import sys
import signal
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
AI_SERVICE_DIR = PROJECT_ROOT / 'ai-service'
BOT_SCRIPT = PROJECT_ROOT / 'whatsapp-bot' / 'index.js'

ai_process = None
bot_process = None

def signal_handler(sig, frame):
    print('\n\n[IRIS] Encerrando...')
    if ai_process:
        ai_process.terminate()
    if bot_process:
        bot_process.terminate()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def main():
    print('=' * 80)
    print('[IRIS] INICIANDO SISTEMA COMPLETO')
    print('=' * 80)
    print()
    
    global ai_process, bot_process
    
    # Inicia AI
    print('[IRIS] Iniciando servico AI...')
    ai_process = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8001'],
        cwd=str(AI_SERVICE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    print(f'[IRIS] [OK] AI iniciado (PID: {ai_process.pid})')
    time.sleep(3)
    
    # Inicia Bot
    print('[IRIS] Iniciando bot WhatsApp...')
    bot_process = subprocess.Popen(
        ['node', str(BOT_SCRIPT)],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    print(f'[IRIS] [OK] Bot iniciado (PID: {bot_process.pid})')
    print()
    print('[IRIS] Sistema iniciado! Escaneie o QR Code quando aparecer.')
    print('[IRIS] Pressione Ctrl+C para encerrar.')
    print()
    
    try:
        while True:
            if ai_process.poll() is not None:
                print('[IRIS] [AVISO] AI parou!')
            if bot_process.poll() is not None:
                print('[IRIS] [AVISO] Bot parou!')
            time.sleep(10)
    except KeyboardInterrupt:
        signal_handler(None, None)

if __name__ == '__main__':
    main()
