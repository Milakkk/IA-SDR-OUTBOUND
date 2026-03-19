"""
Sistema de persistência de conversas e estado do sistema
Salva e carrega o estado completo das conversas em arquivo JSON na raiz do projeto
"""
import json
import os
from typing import Dict, Any, Optional
from datetime import datetime
import threading
from dateutil import parser as dateparser

# Caminho do arquivo de persistência na raiz do projeto
PERSISTENCE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "conversations_state.json")

# Lock para operações thread-safe
_persistence_lock = threading.Lock()


def load_conversation_state() -> Dict[str, Any]:
    """
    Carrega o estado completo das conversas do arquivo JSON.
    
    Returns:
        Dicionário com todo o estado das conversas ou dicionário vazio se arquivo não existir
    """
    if not os.path.exists(PERSISTENCE_FILE):
        print(f"[PERSISTENCE] Arquivo não encontrado: {PERSISTENCE_FILE}")
        return {
            "conversation_history": {},
            "lead_data_by_contact": {},
            "scheduling_sessions": {},
            "seller_by_contact": {},
            "salesforce_leads_by_contact": {},
            "pipedrive_deals_by_contact": {},
            "calendar_events_by_contact": {},
            "last_updated": None
        }
    
    try:
        with _persistence_lock:
            with open(PERSISTENCE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Converte strings ISO de volta para datetime nos slots
                if "scheduling_sessions" in data:
                    for contact_id, session in data["scheduling_sessions"].items():
                        if "proposed_slots" in session and isinstance(session["proposed_slots"], list):
                            for slot in session["proposed_slots"]:
                                if isinstance(slot, dict):
                                    if "start" in slot and isinstance(slot["start"], str):
                                        try:
                                            slot["start"] = dateparser.parse(slot["start"])
                                        except:
                                            pass
                                    if "end" in slot and isinstance(slot["end"], str):
                                        try:
                                            slot["end"] = dateparser.parse(slot["end"])
                                        except:
                                            pass
                print(f"[PERSISTENCE] Estado carregado: {len(data.get('conversation_history', {}))} conversas")
                return data
    except Exception as e:
        print(f"[PERSISTENCE] [ERRO] Erro ao carregar estado: {e}")
        import traceback
        traceback.print_exc()
        return {
            "conversation_history": {},
            "lead_data_by_contact": {},
            "scheduling_sessions": {},
            "seller_by_contact": {},
            "salesforce_leads_by_contact": {},
            "pipedrive_deals_by_contact": {},
            "calendar_events_by_contact": {},
            "last_updated": None
        }


def _serialize_datetime(obj: Any) -> Any:
    """
    Converte objetos datetime para strings ISO para serialização JSON.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: _serialize_datetime(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_datetime(item) for item in obj]
    return obj


def save_conversation_state(
    conversation_history: Dict[str, Any],
    lead_data_by_contact: Dict[str, Any],
    scheduling_sessions: Dict[str, Any],
    seller_by_contact: Dict[str, Any],
    salesforce_leads_by_contact: Dict[str, Any],
    pipedrive_deals_by_contact: Dict[str, Any],
    calendar_events_by_contact: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Salva o estado completo das conversas no arquivo JSON.
    
    Args:
        conversation_history: Histórico de conversas por contato
        lead_data_by_contact: Dados de leads por contato
        scheduling_sessions: Sessões de agendamento por contato
        seller_by_contact: Vendedores roteados por contato
        salesforce_leads_by_contact: Leads do Salesforce por contato
        pipedrive_deals_by_contact: Deals do Pipedrive por contato
    
    Returns:
        True se salvou com sucesso, False caso contrário
    """
    try:
        # Serializa objetos datetime antes de salvar
        state = {
            "conversation_history": _serialize_datetime(conversation_history),
            "lead_data_by_contact": _serialize_datetime(lead_data_by_contact),
            "scheduling_sessions": _serialize_datetime(scheduling_sessions),
            "seller_by_contact": _serialize_datetime(seller_by_contact),
            "salesforce_leads_by_contact": _serialize_datetime(salesforce_leads_by_contact),
            "pipedrive_deals_by_contact": _serialize_datetime(pipedrive_deals_by_contact),
            "calendar_events_by_contact": _serialize_datetime(calendar_events_by_contact or {}),
            "last_updated": datetime.utcnow().isoformat() + "Z"
        }
        
        # Cria diretório se não existir
        os.makedirs(os.path.dirname(PERSISTENCE_FILE), exist_ok=True)
        
        with _persistence_lock:
            # Salva em arquivo temporário primeiro para evitar corrupção
            temp_file = PERSISTENCE_FILE + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            
            # Move arquivo temporário para o arquivo final (operação atômica)
            if os.path.exists(PERSISTENCE_FILE):
                os.replace(temp_file, PERSISTENCE_FILE)
            else:
                os.rename(temp_file, PERSISTENCE_FILE)
        
        print(f"[PERSISTENCE] Estado salvo: {len(conversation_history)} conversas")
        return True
    except Exception as e:
        print(f"[PERSISTENCE] [ERRO] Erro ao salvar estado: {e}")
        import traceback
        traceback.print_exc()
        return False


def get_persistence_file_path() -> str:
    """
    Retorna o caminho do arquivo de persistência.
    
    Returns:
        Caminho completo do arquivo de persistência
    """
    return PERSISTENCE_FILE
