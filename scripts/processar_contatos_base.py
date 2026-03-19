"""
Script para processar contatos do CSV base imediatamente
Detecta novos contatos e prepara para envio
"""
import csv
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set

PROJECT_ROOT = Path(__file__).parent.parent
BASE_CSV = PROJECT_ROOT / 'outbound_data' / 'contatos_base.csv'
FOLLOW_UPS_FILE = PROJECT_ROOT / 'outbound_data' / 'follow_ups.json'

def normalize_phone(phone: str) -> str:
    """Normaliza telefone para formato padrão (apenas dígitos)"""
    return ''.join(filter(str.isdigit, phone))

def read_base_csv() -> List[Dict[str, str]]:
    """Lê o CSV base de contatos"""
    if not BASE_CSV.exists():
        print(f'[ERRO] Arquivo nao encontrado: {BASE_CSV}')
        return []
    
    contacts = []
    try:
        with open(BASE_CSV, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('telefone'):
                    contacts.append(row)
    except Exception as e:
        print(f'[ERRO] Erro ao ler CSV base: {e}')
    
    return contacts

def create_follow_up_entry(contact: Dict[str, str]) -> Dict:
    """Cria entrada no follow_ups.json"""
    phone = normalize_phone(contact.get('telefone', ''))
    now = datetime.now()
    follow_up_1_date = datetime.fromtimestamp(now.timestamp() + 2 * 24 * 60 * 60)  # +2 dias
    follow_up_2_date = datetime.fromtimestamp(follow_up_1_date.timestamp() + 7 * 24 * 60 * 60)  # +7 dias após
    
    return {
        'empresa': contact.get('empresa', ''),
        'contato': contact.get('contato', ''),
        'cargo': contact.get('cargo', ''),
        'sent_at': '',  # Será preenchido quando enviar
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
        'status': 'active',
        'cidade': contact.get('cidade', ''),
        'estado': contact.get('estado', ''),
        'email': contact.get('email', ''),
        'cnpj': contact.get('cnpj', '')
    }

def main():
    """Processa contatos do CSV base"""
    print('=' * 80)
    print('[PROCESSAR] Processando contatos do CSV base')
    print('=' * 80)
    print()
    
    contacts = read_base_csv()
    if not contacts:
        print('[AVISO] Nenhum contato encontrado no CSV base')
        return
    
    print(f'[INFO] {len(contacts)} contato(s) encontrado(s) no CSV base')
    print()
    
    # Carrega follow-ups existentes
    follow_ups = {}
    if FOLLOW_UPS_FILE.exists():
        try:
            with open(FOLLOW_UPS_FILE, 'r', encoding='utf-8') as f:
                follow_ups = json.load(f)
        except Exception:
            pass
    
    processed_count = 0
    new_contacts = []
    
    for contact in contacts:
        phone = normalize_phone(contact.get('telefone', ''))
        if not phone:
            continue
        
        empresa = contact.get('empresa', 'Empresa')
        enviado = contact.get('enviado', '').strip().lower()
        
        # Verifica se já está no sistema
        if phone in follow_ups:
            # Se no CSV estiver marcado como NÃO enviado (vazio, nao, false), mas no JSON estiver enviado:
            # Significa que o usuário quer reenviar (limpou o CSV).
            # Então devemos limpar o status no JSON também.
            if enviado not in ('sim', 'yes', 'true', '1', 's') and follow_ups[phone].get('sent_at'):
                print(f'[INFO] {empresa} ({phone}) - Detectado reset no CSV. Limpando status de envio no sistema...')
                follow_ups[phone]['sent_at'] = ''
                follow_ups[phone]['status'] = 'active'
                # Opcional: Resetar follow-ups também se desejar reinício completo
                # follow_ups[phone]['follow_ups'][0]['sent'] = False
                # follow_ups[phone]['follow_ups'][1]['sent'] = False
                
            # Atualiza sent_at se foi enviado (apenas se no CSV estiver marcado como enviado)
            elif enviado in ('sim', 'yes', 'true', '1', 's') and not follow_ups[phone].get('sent_at'):
                print(f'[INFO] {empresa} ({phone}) - Sincronizando envio do CSV para o sistema')
                follow_ups[phone]['sent_at'] = contact.get('data_envio', datetime.now().isoformat())
            else:
                print(f'[INFO] {empresa} ({phone}) - Ja esta no sistema')
                
            # Sempre atualiza dados cadastrais caso tenham mudado no CSV
            follow_ups[phone]['empresa'] = empresa
            follow_ups[phone]['contato'] = contact.get('contato', follow_ups[phone].get('contato', ''))
            follow_ups[phone]['cidade'] = contact.get('cidade', follow_ups[phone].get('cidade', ''))
            
            # Se limpou o status, adiciona aos contatos processados para salvar o JSON atualizado
            if not follow_ups[phone].get('sent_at'):
                 processed_count += 1
                 
            continue
        
        # Verifica se já foi marcado como enviado
        if enviado in ('sim', 'yes', 'true', '1', 's'):
            print(f'[INFO] {empresa} ({phone}) - Ja marcado como enviado, pulando')
            continue
        
        # Novo contato para processar
        print(f'[NOVO] {empresa} ({phone}) - Adicionando ao sistema...')
        follow_ups[phone] = create_follow_up_entry(contact)
        new_contacts.append(contact)
        processed_count += 1
    
    if processed_count == 0:
        print()
        print('[INFO] Nenhum novo contato para processar')
        print('[INFO] Todos os contatos ja estao no sistema')
        return
    
    # Salva follow-ups
    FOLLOW_UPS_FILE.parent.mkdir(exist_ok=True)
    with open(FOLLOW_UPS_FILE, 'w', encoding='utf-8') as f:
        json.dump(follow_ups, f, indent=2, ensure_ascii=False)
    
    print()
    print(f'[OK] {processed_count} novo(s) contato(s) adicionado(s) ao sistema!')
    print()
    print('[INFO] Para enviar mensagens, execute:')
    print('   node whatsapp-bot/send_from_queue.js')
    print()
    print('[INFO] Ou use o bot principal que esta rodando (index.js)')
    print('   Ele detectara automaticamente e enviara as mensagens')

if __name__ == '__main__':
    main()
