"""
Monitor que verifica o CSV base de contatos e envia mensagens automaticamente
Quando um novo contato é adicionado no CSV base, envia mensagem e adiciona ao tracking
"""
import csv
import json
import time
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set
import subprocess
import requests

try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

# Caminhos
PROJECT_ROOT = Path(__file__).parent.parent
BASE_CSV = PROJECT_ROOT / 'outbound_data' / 'contatos_base.csv'
FOLLOW_UPS_FILE = PROJECT_ROOT / 'outbound_data' / 'follow_ups.json'
OUTBOUND_CSV = PROJECT_ROOT / 'outbound_data' / 'outbound_tracking.csv'
AI_SERVICE_URL = 'http://127.0.0.1:8001'

# Cache de telefones já processados
_processed_phones: Set[str] = set()

def load_processed_phones():
    """Carrega telefones já processados do follow_ups.json"""
    global _processed_phones
    if FOLLOW_UPS_FILE.exists():
        try:
            with open(FOLLOW_UPS_FILE, 'r', encoding='utf-8') as f:
                follow_ups = json.load(f)
                _processed_phones = set(follow_ups.keys())
        except Exception:
            pass

def normalize_phone(phone: str) -> str:
    """Normaliza telefone para formato padrão (apenas dígitos)"""
    return ''.join(filter(str.isdigit, phone))

def read_base_csv() -> List[Dict[str, str]]:
    """Lê o CSV base de contatos"""
    if not BASE_CSV.exists():
        # Cria CSV base se não existir
        BASE_CSV.parent.mkdir(exist_ok=True)
        with open(BASE_CSV, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'telefone', 'empresa', 'contato', 'cargo', 'cidade', 'estado', 
                'email', 'cnpj', 'enviado', 'data_envio'
            ])
            writer.writeheader()
        return []
    
    contacts = []
    try:
        with open(BASE_CSV, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('telefone'):
                    contacts.append(row)
    except Exception as e:
        print(f'[MONITOR] Erro ao ler CSV base: {e}')
    
    return contacts

def check_ai_service():
    """Verifica se o serviço AI está rodando"""
    try:
        response = requests.get(f'{AI_SERVICE_URL}/docs', timeout=2)
        return response.status_code == 200
    except:
        return False

def send_whatsapp_message(phone: str, message: str) -> bool:
    """
    Envia mensagem via WhatsApp usando o bot
    Retorna True se enviado com sucesso
    """
    # Normaliza telefone
    phone_normalized = normalize_phone(phone)
    
    # Verifica se o bot está rodando e envia via API ou arquivo de controle
    # Por enquanto, vamos usar o método de atualizar follow_ups.json
    # O bot principal (index.js) pode ler isso e enviar
    
    # Alternativa: usar send_test_outbound.js via subprocess
    # Mas melhor: criar um endpoint no bot ou usar arquivo de controle
    
    # Por enquanto, vamos apenas registrar no follow_ups.json
    # O usuário pode usar send_test_outbound.js manualmente ou
    # podemos criar um script Node.js que lê um arquivo de fila
    
    return True

def create_follow_up_entry(contact: Dict[str, str]) -> Dict:
    """Cria entrada de outbound sem agendar follow-ups."""
    phone = normalize_phone(contact.get('telefone', ''))

    return {
        'empresa': contact.get('empresa', ''),
        'contato': contact.get('contato', ''),
        'cargo': contact.get('cargo', ''),
        'sent_at': '',
        'responded': False,
        'status': 'active',
        'sector': contact.get('setor') or contact.get('sector') or 'grow',
        'cidade': contact.get('cidade', ''),
        'estado': contact.get('estado', ''),
        'email': contact.get('email', ''),
        'cnpj': contact.get('cnpj', '')
    }

def process_new_contacts():
    """Processa novos contatos do CSV base"""
    contacts = read_base_csv()
    if not contacts:
        return 0
    
    sent_true = ('sim', 'yes', 'true', '1', 's')
    ok_true = ('ok', 'sim', 'yes', 'true', '1', 's')

    contacts_updated = False
    follow_ups_updated = False
    new_contacts: List[Dict[str, str]] = []

    # Carrega follow-ups existentes (uma vez)
    follow_ups = {}
    if FOLLOW_UPS_FILE.exists():
        try:
            with open(FOLLOW_UPS_FILE, 'r', encoding='utf-8') as f:
                follow_ups = json.load(f)
        except Exception:
            pass
    
    for contact in contacts:
        phone = normalize_phone(contact.get('telefone', ''))
        if not phone:
            continue

        ok_value = str(contact.get('ok', '')).strip().lower()
        if ok_value not in ok_true:
            continue

        enviado_raw = contact.get('enviado', '')
        enviado = str(enviado_raw).strip().lower()
        fu = follow_ups.get(phone)

        # Se o CSV já marca como enviado, garante sent_at no follow_ups quando possível
        if enviado in sent_true:
            _processed_phones.add(phone)
            if isinstance(fu, dict) and not str(fu.get('sent_at', '')).strip():
                fu['sent_at'] = contact.get('data_envio') or datetime.now().isoformat()
                follow_ups[phone] = fu
                follow_ups_updated = True
            continue

        # Se já existe no follow_ups, só sincroniza status (sent_at -> CSV) se aplicável
        if isinstance(fu, dict):
            _processed_phones.add(phone)
            sent_at = str(fu.get('sent_at', '')).strip()
            if sent_at and enviado not in sent_true:
                contact['enviado'] = 'Sim'
                contact['data_envio'] = sent_at
                contacts_updated = True
            elif (not sent_at) and enviado not in sent_true and str(enviado_raw).strip().lower() != 'fila':
                contact['enviado'] = 'Fila'
                contacts_updated = True
            continue

        # Novo contato (ainda não está no follow_ups)
        new_contacts.append(contact)

    if new_contacts:
        print(f'\n[MONITOR] {len(new_contacts)} novo(s) contato(s) encontrado(s)!')

    processed_count = 0

    for contact in new_contacts:
        phone = normalize_phone(contact.get('telefone', ''))
        empresa = contact.get('empresa', 'Empresa')
        contato = contact.get('contato', 'Contato')
        
        print(f'\n[MONITOR] Processando: {empresa} ({phone})...')
        
        # Cria entrada no follow_ups.json
        follow_ups[phone] = create_follow_up_entry(contact)
        follow_ups_updated = True
        
        # Adiciona ao cache
        _processed_phones.add(phone)
        processed_count += 1
        
        print(f'   [OK] Registrado no sistema de follow-ups')
        print(f'   [INFO] Aguardando envio automático pelo bot (sent_at vazio)')

        enviado = str(contact.get('enviado', '')).strip().lower()
        if enviado not in sent_true:
            contact['enviado'] = 'Fila'
            contacts_updated = True
    
    if follow_ups_updated:
        FOLLOW_UPS_FILE.parent.mkdir(exist_ok=True)
        with open(FOLLOW_UPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(follow_ups, f, indent=2, ensure_ascii=False)

    if contacts_updated:
        update_base_csv(contacts)

    if processed_count > 0 or contacts_updated:
        update_tracking_csv()
    
    return processed_count

def update_base_csv(contacts: List[Dict[str, str]]):
    """Atualiza o CSV base com status de envio - preserva todos os contatos"""
    try:
        # Lê todos os contatos do CSV (incluindo os sem 'ok')
        all_contacts = []
        if BASE_CSV.exists():
            with open(BASE_CSV, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                all_contacts = list(reader)
        
        def _update_key(row: Dict[str, str]) -> tuple[str, str]:
            return (
                normalize_phone(row.get('telefone', '')),
                str(row.get('ok', '')).strip().lower(),
            )

        contacts_dict = {_update_key(c): c for c in contacts}
        
        # Atualiza os contatos processados
        for contact in all_contacts:
            key = _update_key(contact)
            if key in contacts_dict:
                contact.update(contacts_dict[key])
        
        # Escreve de volta
        with open(BASE_CSV, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = [
                'telefone', 'empresa', 'contato', 'cargo', 'cidade', 'estado',
                'email', 'cnpj', 'enviado', 'data_envio', 'ok'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            # Garante que todos os campos existam
            for contact in all_contacts:
                row = {field: contact.get(field, '') for field in fieldnames}
                writer.writerow(row)
    except Exception as e:
        print(f'[MONITOR] Erro ao atualizar CSV base: {e}')

def update_tracking_csv():
    """Atualiza o CSV de tracking"""
    try:
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / 'scripts' / 'track_outbound_to_csv.py')],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        if result.returncode == 0:
            print('   [OK] CSV de tracking atualizado')
        else:
            print(f'   [AVISO] Erro ao atualizar tracking: {result.stderr[:200]}')
    except Exception as e:
        print(f'   [AVISO] Erro ao atualizar tracking: {str(e)[:200]}')

def main():
    """Função principal - monitora CSV base continuamente"""
    print('=' * 80)
    print('[MONITOR] MONITOR DE CONTATOS BASE - OUTBOUND')
    print('=' * 80)
    print()
    print('Este script monitora o arquivo: outbound_data/contatos_base.csv')
    print('Quando você adicionar um novo contato no CSV, o sistema:')
    print('  1. Detecta o novo contato')
    print('  2. Cria registro no sistema de follow-ups')
    print('  3. Marca como enviado no CSV base')
    print('  4. Atualiza o CSV de tracking')
    print()
    print('📁 CSV Base: outbound_data/contatos_base.csv')
    print('📁 CSV Tracking: outbound_data/outbound_tracking.csv')
    print()
    print('[INFO] Pressione Ctrl+C para encerrar')
    print()
    
    # Carrega telefones já processados
    load_processed_phones()
    print(f'[MONITOR] {len(_processed_phones)} contato(s) já processado(s)')
    print()
    
    # Verifica serviço AI
    if not check_ai_service():
        print('[AVISO] Serviço AI não está rodando (http://127.0.0.1:8001)')
        print('   O monitor continuará, mas algumas funcionalidades podem não funcionar')
        print()
    
    check_interval = 10  # Verifica a cada 10 segundos
    last_check = time.time()
    
    try:
        while True:
            current_time = time.time()
            
            if current_time - last_check >= check_interval:
                processed = process_new_contacts()
                if processed > 0:
                    print(f'\n[MONITOR] {processed} contato(s) processado(s) com sucesso!')
                last_check = current_time
            
            time.sleep(5)  # Verifica a cada 5 segundos
            
    except KeyboardInterrupt:
        print('\n\n[MONITOR] Encerrando monitor...')
        sys.exit(0)

if __name__ == '__main__':
    main()
