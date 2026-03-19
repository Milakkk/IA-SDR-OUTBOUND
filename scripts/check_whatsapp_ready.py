"""Verifica se o WhatsApp está pronto para enviar mensagens"""
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
READY_FILE = PROJECT_ROOT / 'whatsapp_ready.json'

def is_whatsapp_ready():
    """Verifica se o WhatsApp está pronto"""
    if not READY_FILE.exists():
        return False
    
    try:
        with open(READY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('ready', False)
    except:
        return False

if __name__ == '__main__':
    import sys
    sys.exit(0 if is_whatsapp_ready() else 1)
