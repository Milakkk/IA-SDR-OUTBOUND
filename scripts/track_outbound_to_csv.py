"""
Sistema de tracking de leads outbound para CSV
Salva informações completas sobre cada contato outbound em CSV
"""
import csv
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

OUTBOUND_CSV = Path(__file__).parent.parent / 'outbound_data' / 'outbound_tracking.csv'
FOLLOW_UPS_FILE = Path(__file__).parent.parent / 'outbound_data' / 'follow_ups.json'
CONVERSATIONS_STATE = Path(__file__).parent.parent / 'conversations_state.json'


def load_follow_ups() -> Dict[str, Any]:
    """Carrega dados de follow-ups"""
    if not FOLLOW_UPS_FILE.exists():
        return {}
    try:
        with open(FOLLOW_UPS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def load_conversations_state() -> Dict[str, Any]:
    """Carrega estado das conversas"""
    if not CONVERSATIONS_STATE.exists():
        return {}
    try:
        with open(CONVERSATIONS_STATE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def get_lead_data_from_state(phone: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai dados do lead do estado das conversas"""
    lead_data = state.get('lead_data_by_contact', {}).get(phone, {})
    scheduling = state.get('scheduling_sessions', {}).get(phone, {})
    salesforce = state.get('salesforce_leads_by_contact', {}).get(phone, {})
    
    return {
        'lead_data': lead_data,
        'scheduling': scheduling,
        'salesforce': salesforce
    }


def determine_interest_level(lead_data: Dict[str, Any], scheduling: Dict[str, Any], responded: bool) -> str:
    """Determina nível de interesse baseado nos dados"""
    if not responded:
        return 'Sem resposta'
    
    # Se agendou reunião, é alto interesse
    if scheduling.get('confirmed') or scheduling.get('event_id'):
        return 'Alto interesse (agendou)'
    
    # Se criou lead no Salesforce, é médio interesse
    if lead_data:
        return 'Médio interesse (qualificado)'
    
    # Se apenas respondeu
    return 'Baixo interesse (apenas respondeu)'


def needs_followup(follow_up_data: Dict[str, Any]) -> bool:
    """Verifica se precisa de follow-up"""
    if follow_up_data.get('responded'):
        return False
    
    status = follow_up_data.get('status', 'active')
    if status == 'lost':
        return False
    
    # Verifica se há follow-ups pendentes
    follow_ups = follow_up_data.get('follow_ups', [])
    for fu in follow_ups:
        if not fu.get('sent'):
            scheduled = datetime.fromisoformat(fu.get('scheduled_for', '').replace('Z', '+00:00'))
            if datetime.now(scheduled.tzinfo) >= scheduled:
                return True
    
    return False


def generate_outbound_csv():
    """Gera CSV completo com todos os dados de outbound"""
    follow_ups = load_follow_ups()
    conversations_state = load_conversations_state()
    
    # Cria diretório se não existir
    OUTBOUND_CSV.parent.mkdir(exist_ok=True)
    
    # Campos do CSV
    fieldnames = [
        'telefone',
        'empresa',
        'contato',
        'cargo',
        'cidade',
        'estado',
        'email',
        'cnpj',
        'mensagem_inicial_enviada',
        'data_mensagem_inicial',
        'respondeu',
        'data_resposta',
        'agendou_reuniao',
        'data_agendamento',
        'link_reuniao',
        'nivel_interesse',
        'necessita_followup',
        'follow_up_1_enviado',
        'data_follow_up_1',
        'follow_up_2_enviado',
        'data_follow_up_2',
        'status',
        'lead_salesforce_id',
        'cultivo',
        'estrutura',
        'area',
        'observacoes',
        'ultima_atualizacao'
    ]
    
    rows = []
    
    for phone, follow_up_data in follow_ups.items():
        # Dados do follow-up
        empresa = follow_up_data.get('empresa', '')
        contato = follow_up_data.get('contato', '')
        cargo = follow_up_data.get('cargo', '')
        sent_at = follow_up_data.get('sent_at', '')
        responded = follow_up_data.get('responded', False)
        responded_at = follow_up_data.get('responded_at', '')
        status = follow_up_data.get('status', 'active')
        lead_id = follow_up_data.get('salesforce_lead_id', '')
        
        # Dados do lead do estado das conversas
        state_data = get_lead_data_from_state(phone, conversations_state)
        lead_data = state_data.get('lead_data', {})
        scheduling = state_data.get('scheduling', {})
        salesforce = state_data.get('salesforce', {})
        
        # Extrai informações do lead
        cidade = lead_data.get('cidade') or lead_data.get('city') or ''
        estado = lead_data.get('estado') or lead_data.get('state') or lead_data.get('uf') or ''
        email = lead_data.get('email') or ''
        cnpj = lead_data.get('cnpj') or ''
        cultivo = lead_data.get('cultivo') or ''
        estrutura = lead_data.get('estrutura') or ''
        area = lead_data.get('area') or lead_data.get('tamanho') or ''
        
        # Informações de agendamento
        agendou = bool(scheduling.get('confirmed') or scheduling.get('event_id'))
        data_agendamento = ''
        link_reuniao = ''
        if scheduling.get('confirmed_slot'):
            slot = scheduling.get('confirmed_slot', {})
            if isinstance(slot, dict):
                start = slot.get('start', '')
                if isinstance(start, str):
                    data_agendamento = start
                link_reuniao = scheduling.get('meet_link', '')
        
        # Follow-ups
        follow_ups_list = follow_up_data.get('follow_ups', [])
        fu1_sent = False
        fu1_date = ''
        fu2_sent = False
        fu2_date = ''
        
        for fu in follow_ups_list:
            if fu.get('message_type') == 'follow_up_1':
                fu1_sent = fu.get('sent', False)
                fu1_date = fu.get('sent_at', '')
            elif fu.get('message_type') == 'follow_up_2':
                fu2_sent = fu.get('sent', False)
                fu2_date = fu.get('sent_at', '')
        
        # Nível de interesse
        nivel_interesse = determine_interest_level(lead_data, scheduling, responded)
        
        # Necessita follow-up
        necessita_fu = needs_followup(follow_up_data)
        
        # Observações
        observacoes_parts = []
        if lead_data:
            observacoes_parts.append(f"Dados coletados: {json.dumps(lead_data, ensure_ascii=False)}")
        if scheduling:
            observacoes_parts.append(f"Agendamento: {json.dumps(scheduling, ensure_ascii=False, default=str)}")
        
        row = {
            'telefone': phone,
            'empresa': empresa,
            'contato': contato,
            'cargo': cargo,
            'cidade': cidade,
            'estado': estado,
            'email': email,
            'cnpj': cnpj,
            'mensagem_inicial_enviada': 'Sim' if sent_at else 'Não',
            'data_mensagem_inicial': sent_at,
            'respondeu': 'Sim' if responded else 'Não',
            'data_resposta': responded_at,
            'agendou_reuniao': 'Sim' if agendou else 'Não',
            'data_agendamento': data_agendamento,
            'link_reuniao': link_reuniao,
            'nivel_interesse': nivel_interesse,
            'necessita_followup': 'Sim' if necessita_fu else 'Não',
            'follow_up_1_enviado': 'Sim' if fu1_sent else 'Não',
            'data_follow_up_1': fu1_date,
            'follow_up_2_enviado': 'Sim' if fu2_sent else 'Não',
            'data_follow_up_2': fu2_date,
            'status': status,
            'lead_salesforce_id': lead_id or salesforce.get('id', ''),
            'cultivo': cultivo,
            'estrutura': estrutura,
            'area': area,
            'observacoes': ' | '.join(observacoes_parts),
            'ultima_atualizacao': datetime.now().isoformat()
        }
        
        rows.append(row)
    
    # Escreve CSV
    file_exists = OUTBOUND_CSV.exists()
    with open(OUTBOUND_CSV, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)
    
    print(f"[CSV] Arquivo atualizado: {OUTBOUND_CSV}")
    print(f"[CSV] Total de registros: {len(rows)}")
    return OUTBOUND_CSV


if __name__ == '__main__':
    generate_outbound_csv()
