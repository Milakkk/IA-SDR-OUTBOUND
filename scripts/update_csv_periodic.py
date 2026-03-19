"""
Script para atualizar CSV periodicamente (pode ser executado em cron/task scheduler)
"""
import time
import sys
from pathlib import Path

# Adiciona o diretório do script ao path
sys.path.insert(0, str(Path(__file__).parent))

from track_outbound_to_csv import generate_outbound_csv

if __name__ == '__main__':
    print(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] Atualizando CSV de tracking...')
    try:
        csv_path = generate_outbound_csv()
        print(f'✅ CSV atualizado: {csv_path}')
    except Exception as e:
        print(f'❌ Erro ao atualizar CSV: {e}')
        sys.exit(1)
