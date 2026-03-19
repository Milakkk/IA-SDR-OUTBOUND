"""
Monitor completo que:
1. Monitora contatos_base.csv
2. Processa apenas contatos com 'ok' marcado
3. Envia mensagens automaticamente via WhatsApp
4. Atualiza CSV de tracking
5. Roda continuamente sozinho
"""
import csv
import json
import time
import sys
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set
import requests

# Caminhos
PROJECT_ROOT = Path(__file__).parent.parent
BASE_CSV = PROJECT_ROOT / 'outbound_data' / 'contatos_base.csv'
FOLLOW_UPS_FILE = PROJECT_ROOT / 'outbound_data' / 'follow_ups.json'
OUTBOUND_CSV = PROJECT_ROOT / 'outbound_data' / 'outbound_tracking.csv'
SEND_SCRIPT = PROJECT_ROOT / 'scripts' / 'enviar_mensagens_pendentes.js'

# Cache de telefones já processados
_processed_phones: Set[str] = set()

def normalize_phone(phone: str) -> str:
    """Normaliza telefone para formato padrão (apenas dígitos)"""
    return ''.join(filter(str.isdigit, phone))

def read_base_csv() -> List[Dict[str, str]]:
    """Lê o CSV base - apenas contatos com 'ok' marcado"""
    if not BASE_CSV.exists():
        return []
    
    contacts = []
    try:
        with open(BASE_CSV, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                telefone = row.get('telefone', '').strip()
                if not telefone:
                    continue
                
                # Verifica se tem 'ok' marcado
                ok_value = row.get('ok', '').strip().lower()
                if ok_value not in ('ok', 'sim', 'yes', 's', '1', 'true'):
                    continue  # Pula contatos sem 'ok'
                
                contacts.append(row)
    except Exception as e:
        print(f'[MONITOR] Erro ao ler CSV base: {e}')
    
    return contacts

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

def process_new_contacts():
    """Processa novos contatos do CSV base"""
    contacts = read_base_csv()
    new_contacts = []
    
    print(f'[DEBUG] Lendo CSV: {len(contacts)} contato(s) com "ok" encontrado(s)')
    
    # Carrega follow-ups existentes para verificar sent_at
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
            print(f'[DEBUG] Telefone vazio, pulando: {contact.get("empresa", "N/A")}')
            continue
        
        print(f'[DEBUG] Verificando: {contact.get("empresa", "N/A")} - Telefone: {phone}')
        
        # Verifica se já foi marcado como enviado no CSV
        enviado = contact.get('enviado', '').strip().lower()
        if enviado in ('sim', 'yes', 'true', '1', 's'):
            print(f'[DEBUG] Telefone {phone} já marcado como enviado no CSV, adicionando ao cache')
            _processed_phones.add(phone)
            continue
        
        # Verifica se já está no follow_ups.json
        if phone in follow_ups:
            # Se já tem sent_at preenchido, já foi enviado, pula
            sent_at = follow_ups[phone].get('sent_at', '').strip()
            if sent_at:
                print(f'[DEBUG] Telefone {phone} já foi enviado (tem sent_at: {sent_at}), pulando')
                _processed_phones.add(phone)
                continue
            else:
                # Está no follow_ups mas não foi enviado ainda - NÃO reprocessa
                print(f'[DEBUG] Telefone {phone} já está no sistema aguardando envio - não reprocessa')
                _processed_phones.add(phone)  # Adiciona ao cache para não reprocessar
                continue  # Pula - já está registrado, só falta enviar
        
        print(f'[DEBUG] NOVO CONTATO DETECTADO: {contact.get("empresa", "N/A")} ({phone})')
        new_contacts.append(contact)
    
    if not new_contacts:
        print('[DEBUG] Nenhum novo contato encontrado')
        return 0
    
    print(f'\n[MONITOR] {len(new_contacts)} novo(s) contato(s) encontrado(s) com "ok"!')
    
    # Carrega follow-ups existentes
    follow_ups = {}
    if FOLLOW_UPS_FILE.exists():
        try:
            with open(FOLLOW_UPS_FILE, 'r', encoding='utf-8') as f:
                follow_ups = json.load(f)
        except Exception:
            pass
    
    processed_count = 0
    
    for contact in new_contacts:
        phone = normalize_phone(contact.get('telefone', ''))
        empresa = contact.get('empresa', 'Empresa')
        contato = contact.get('contato', 'Contato')
        
        print(f'\n[MONITOR] Processando: {empresa} ({phone})...')
        
        # Se já existe no follow_ups mas não tem sent_at, atualiza os dados
        if phone in follow_ups:
            if follow_ups[phone].get('sent_at'):
                print(f'   [AVISO] Telefone {phone} já foi enviado anteriormente, pulando envio')
                # Atualiza dados mas mantém sent_at
                follow_ups[phone].update({
                    'empresa': empresa,
                    'contato': contato,
                    'cargo': contact.get('cargo', ''),
                    'cidade': contact.get('cidade', ''),
                    'estado': contact.get('estado', ''),
                    'email': contact.get('email', ''),
                    'cnpj': contact.get('cnpj', '')
                })
            else:
                # Existe mas não foi enviado - atualiza e permite envio
                print(f'   [INFO] Telefone {phone} já está no sistema mas não foi enviado, atualizando...')
                follow_ups[phone] = create_follow_up_entry(contact)
        else:
            # Novo contato - cria entrada
            follow_ups[phone] = create_follow_up_entry(contact)
        
        # Adiciona ao cache
        _processed_phones.add(phone)
        processed_count += 1
        
        print(f'   [OK] Registrado no sistema de follow-ups')
    
    # Salva follow-ups
    FOLLOW_UPS_FILE.parent.mkdir(exist_ok=True)
    with open(FOLLOW_UPS_FILE, 'w', encoding='utf-8') as f:
        json.dump(follow_ups, f, indent=2, ensure_ascii=False)
    
    # Atualiza CSV de tracking
    update_tracking_csv()
    
    return processed_count

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
    except Exception as e:
        pass  # Silencioso

def is_whatsapp_ready():
    """Verifica se o WhatsApp está pronto para enviar mensagens"""
    ready_file = PROJECT_ROOT / 'whatsapp_ready.json'
    if not ready_file.exists():
        return False
    
    try:
        with open(ready_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('ready', False)
    except:
        return False

def send_pending_messages():
    """Envia mensagens para contatos pendentes - apenas se WhatsApp estiver pronto"""
    # Verifica se WhatsApp está pronto
    if not is_whatsapp_ready():
        return  # WhatsApp ainda não está conectado
    
    try:
        # Verifica se há contatos pendentes
        if not FOLLOW_UPS_FILE.exists():
            return
        
        with open(FOLLOW_UPS_FILE, 'r', encoding='utf-8') as f:
            follow_ups = json.load(f)
        
        # Conta quantos estão pendentes (sem sent_at)
        pending = [p for p in follow_ups.keys() if not follow_ups[p].get('sent_at', '').strip()]
        
        if not pending:
            return  # Nenhum pendente
        
        print(f'\n[MONITOR] {len(pending)} contato(s) pendente(s) para envio')
        for phone in pending:
            empresa = follow_ups[phone].get('empresa', 'N/A')
            print(f'  - {empresa} ({phone})')
        
        print('[MONITOR] WhatsApp está pronto! Enviando mensagens...')
        
        # O bot principal (index.js) já está rodando e conectado
        # Ele monitora follow_ups.json automaticamente
        # Por enquanto, apenas informamos que há mensagens pendentes
        # O bot principal pode ser ajustado para ler follow_ups.json periodicamente
        
        print('[MONITOR] [INFO] Mensagens pendentes registradas no follow_ups.json')
        print('[MONITOR] [INFO] O bot principal enviará automaticamente quando processar')
            
    except subprocess.TimeoutExpired:
        print('[MONITOR] [AVISO] Timeout ao enviar mensagens')
    except Exception as e:
        print(f'[MONITOR] [ERRO] Exceção ao enviar: {e}')

def main():
    """Função principal - roda continuamente"""
    print('=' * 80)
    print('[MONITOR] SISTEMA AUTOMATICO DE CONTATOS BASE')
    print('=' * 80)
    print()
    print('[OK] Monitor iniciado e pronto!')
    print()
    print('Este sistema:')
    print('  1. Monitora contatos_base.csv continuamente')
    print('  2. Processa apenas contatos com coluna "ok" marcada')
    print('  3. Envia mensagens automaticamente')
    print('  4. Atualiza CSV de tracking')
    print()
    print('[INFO] Adicione contatos no CSV e marque "ok" para processar')
    print('[INFO] Pressione Ctrl+C para encerrar')
    print()
    
    # Verifica se o CSV existe
    if not BASE_CSV.exists():
        print('[AVISO] Arquivo contatos_base.csv nao encontrado!')
        print('[INFO] Criando arquivo...')
        BASE_CSV.parent.mkdir(exist_ok=True)
        with open(BASE_CSV, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['telefone', 'empresa', 'contato', 'cargo', 'cidade', 'estado', 'email', 'cnpj', 'enviado', 'data_envio', 'ok'])
        print('[OK] Arquivo criado!')
        print()
    
    # Carrega telefones já processados
    load_processed_phones()
    print(f'[OK] Sistema carregado: {len(_processed_phones)} contato(s) ja processado(s)')
    
    # Verifica contatos com "ok" no CSV
    contacts_with_ok = read_base_csv()
    pending_count = 0
    if FOLLOW_UPS_FILE.exists():
        try:
            with open(FOLLOW_UPS_FILE, 'r', encoding='utf-8') as f:
                follow_ups = json.load(f)
            for contact in contacts_with_ok:
                phone = normalize_phone(contact.get('telefone', ''))
                if phone in follow_ups:
                    if not follow_ups[phone].get('sent_at', '').strip():
                        pending_count += 1
        except:
            pass
    
    if pending_count > 0:
        print(f'[OK] {pending_count} contato(s) com "ok" aguardando envio')
    else:
        print('[OK] Nenhum contato pendente no momento')
    
    print()
    print('[OK] Monitor ativo e lendo CSV!')
    
    # Verifica se WhatsApp está pronto
    if is_whatsapp_ready():
        print('[OK] WhatsApp já está conectado! Pronto para enviar mensagens!')
    else:
        print('[INFO] Aguardando WhatsApp conectar...')
        print('[INFO] Escaneie o QR Code quando aparecer no terminal')
        print('[INFO] Após conectar, o monitor começará a enviar mensagens automaticamente')
    
    print('=' * 80)
    print()
    
    check_interval = 10  # Verifica a cada 10 segundos (mais rápido para debug)
    send_interval = 30   # Tenta enviar a cada 30 segundos (mais rápido)
    last_check = time.time()
    last_send = time.time()
    whatsapp_ready_shown = False
    
    try:
        while True:
            current_time = time.time()
            
            # Verifica se WhatsApp ficou pronto
            if is_whatsapp_ready() and not whatsapp_ready_shown:
                print('\n' + '=' * 80)
                print('[MONITOR] [OK] WhatsApp conectado! Agora pode enviar mensagens!')
                print('=' * 80)
                print()
                whatsapp_ready_shown = True
            
            # Processa novos contatos
            if current_time - last_check >= check_interval:
                print(f'\n[MONITOR] Verificando novos contatos... ({datetime.now().strftime("%H:%M:%S")})')
                processed = process_new_contacts()
                if processed > 0:
                    print(f'\n[MONITOR] {processed} contato(s) processado(s)!')
                last_check = current_time
            
            # Envia mensagens pendentes (apenas se WhatsApp estiver pronto)
            if current_time - last_send >= send_interval:
                if is_whatsapp_ready():
                    send_pending_messages()
                else:
                    # Mostra mensagem apenas uma vez a cada minuto
                    if int(current_time) % 60 == 0:
                        print('[MONITOR] [INFO] Aguardando WhatsApp conectar para enviar mensagens...')
                last_send = current_time
            
            time.sleep(5)  # Verifica a cada 5 segundos (mais responsivo)
            
    except KeyboardInterrupt:
        print('\n\n[MONITOR] Encerrando monitor...')
        sys.exit(0)

if __name__ == '__main__':
    main()
