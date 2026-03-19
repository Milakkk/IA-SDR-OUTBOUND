import json
import os
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
WHATSAPP_READY_FILE = PROJECT_ROOT / 'whatsapp_ready.json'
FOLLOW_UPS_FILE = PROJECT_ROOT / 'outbound_data' / 'follow_ups.json'

def check_status():
    print("=" * 50)
    print("STATUS DO BOT WHATSAPP")
    print("=" * 50)
    
    # 1. Check WhatsApp Ready Status
    if WHATSAPP_READY_FILE.exists():
        try:
            with open(WHATSAPP_READY_FILE, 'r', encoding='utf-8') as f:
                ready_data = json.load(f)
                ready = ready_data.get('ready', False)
                at = ready_data.get('at', 'Desconhecido')
                event = ready_data.get('event', 'Desconhecido')
                
                status_str = "[V] PRONTO" if ready else "[X] AGUARDANDO QR CODE / DESCONECTADO"
                print(f"Status: {status_str}")
                print(f"Ultima atualizacao: {at}")
                print(f"Ultimo evento: {event}")
        except Exception as e:
            print(f"Erro ao ler whatsapp_ready.json: {e}")

    else:
        print("Status: ❓ Arquivo de status (whatsapp_ready.json) não encontrado.")

    print("-" * 50)
    
    # 2. Check Pending Messages
    if FOLLOW_UPS_FILE.exists():
        try:
            with open(FOLLOW_UPS_FILE, 'r', encoding='utf-8') as f:
                follow_ups = json.load(f)
                
                pending = [p for p, data in follow_ups.items() if not data.get('sent_at')]
                sent = [p for p, data in follow_ups.items() if data.get('sent_at')]
                
                print(f"Mensagens na fila (pendentes): {len(pending)}")
                print(f"Mensagens já enviadas: {len(sent)}")
                
                if pending:
                    print("\nPróximos na fila:")
                    for p in pending[:5]:
                        empresa = follow_ups[p].get('empresa', 'N/A')
                        print(f" - {p} ({empresa})")
                    if len(pending) > 5:
                        print(f" ... e mais {len(pending) - 5} contatos.")
        except Exception as e:
            print(f"Erro ao ler follow_ups.json: {e}")
    else:
        print("Fila: Arquivo follow_ups.json não encontrado.")

    print("=" * 50)
    print("\nPara iniciar o bot e ver o QR code, rode:")
    print("python main.py")
    print("=" * 50)

if __name__ == "__main__":
    check_status()
