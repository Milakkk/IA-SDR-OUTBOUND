"""
Sistema de análise de ações baseado na resposta do DeepSeek
"""
import json
import re
from typing import Dict, Any, List, Optional
import deepseek_client

def analyze_actions(
    user_message: str,
    conversation_history: List[Dict[str, str]],
    contact_id: str
) -> Dict[str, Any]:
    """
    Analisa a mensagem do usuário e detecta ações a serem executadas.
    
    Args:
        user_message: Mensagem do usuário (não a resposta do assistente)
        conversation_history: Histórico completo da conversa
        contact_id: ID do contato
    
    Returns:
        Dicionário com ações detectadas:
        {
            "needs_scheduling": bool,
            "scheduling_info": {"date": "...", "time": "...", "text": "..."},
            "needs_routing": bool,
            "no_meeting_wanted": bool,
            "needs_slack_notification": bool,
            "slack_message": "..."
        }
    """
    actions = {
        "needs_scheduling": False,
        "scheduling_info": {},
        "needs_routing": False,
        "no_meeting_wanted": False,
        "urgent_meeting": False,
        "confirmed_scheduling": False,
        "needs_slack_notification": False,
        "slack_message": ""
    }
    
    message_lower = user_message.lower()
    conversation_text = "\n".join([
        f"{msg['role'].upper()}: {msg['content']}"
        for msg in conversation_history[-5:]  # Últimas 5 mensagens para contexto
    ])
    last_assistant_message = next(
        (msg.get("content", "") for msg in reversed(conversation_history) if msg.get("role") == "assistant"),
        "",
    )
    
    print(f"[ACTION_ANALYZER] Analisando mensagem: {user_message[:100]}...")
    print(f"[ACTION_ANALYZER] Mensagem (lower): {message_lower[:100]}...")
    
    # Detecta se lead não quer reunião
    no_meeting_phrases = [
        "não preciso", "não preciso de reunião", "não quero reunião",
        "só quero orçamento", "só quero o orçamento", "apenas orçamento",
        "só orçamento", "não quero agendar", "não precisa agendar"
    ]
    
    if any(phrase in message_lower for phrase in no_meeting_phrases):
        actions["no_meeting_wanted"] = True
        actions["needs_slack_notification"] = True
    
    # Detecta reunião rápida/urgente
    urgent_phrases = [
        "rápido", "rapido", "urgente", "o quanto antes", "quanto antes",
        "hoje", "agora", "preciso urgente", "preciso rápido", "preciso rapido",
        "resposta rápida", "resposta rapida", "urgência", "urgencia"
    ]
    
    if any(phrase in message_lower for phrase in urgent_phrases):
        actions["urgent_meeting"] = True
        actions["needs_slack_notification"] = True
        print(f"[ACTION_ANALYZER] ⚠️ Reunião URGENTE detectada!")
    
    # Detecta confirmação de horário
    confirmation_phrases = [
        "sim", "pode ser", "funciona", "ok", "beleza", "confirmo", "pode",
        "pode sim", "tá bom", "ta bom", "perfeito", "ótimo", "otimo",
        "combinado", "pode marcar", "pode agendar"
    ]
    
    schedule_context = bool(re.search(r"\b(reunião|reuniao|agenda|agendar|horário|horario|que tal|funciona pra você|pode ser)\b", last_assistant_message.lower()))

    if schedule_context and any(phrase in message_lower for phrase in confirmation_phrases):
        actions["confirmed_scheduling"] = True
        print(f"[ACTION_ANALYZER] ✅ Confirmação de agendamento detectada!")
    
    # Detecta agendamento - analisa a mensagem do USUÁRIO
    scheduling_keyword_patterns = [
        r"\bagendar\b",
        r"\breuni[aã]o\b",
        r"\bhor[aá]rio\b",
        r"\bdispon[ií]vel\b",
        r"\b(segunda|terça|terca|quarta|quinta|sexta|sábado|sabado|domingo)\b",
        r"\b(amanhã|amanha|hoje)\b",
        r"\b(pode ser|que tal)\b",
        r"\b(manhã|manha|tarde|noite)\b",
        r"\b\d{1,2}:\d{2}\b",
        r"\b\d{1,2}\s*h\b",
        r"\b\d{1,2}h\d{0,2}\b",
        r"\b(?:às|as)\s+\d{1,2}(?::\d{2})?\b",
    ]
    
    # Padrões específicos para detecção
    scheduling_patterns = [
        r"pode ser.*\d{1,2}h",  # "pode ser amanhã às 14h"
        r"que tal.*\d{1,2}h",   # "que tal segunda às 10h"
        r"amanhã.*\d{1,2}h",    # "amanhã às 14h"
        r"segunda.*\d{1,2}h",   # "segunda às 10h"
        r"terça.*\d{1,2}h",     # "terça às 14h"
        r"quarta.*\d{1,2}h",    # "quarta às 15h"
        r"quinta.*\d{1,2}h",    # "quinta às 16h"
        r"sexta.*\d{1,2}h",     # "sexta às 9h"
    ]
    
    matching_keywords = [pattern for pattern in scheduling_keyword_patterns if re.search(pattern, message_lower)]
    matching_patterns = [pattern for pattern in scheduling_patterns if re.search(pattern, message_lower)]
    
    if matching_keywords or matching_patterns:
        print(f"[ACTION_ANALYZER] ✅ Palavras-chave de agendamento encontradas: {matching_keywords}")
        if matching_patterns:
            print(f"[ACTION_ANALYZER] ✅ Padrões de agendamento encontrados: {matching_patterns}")
        actions["needs_scheduling"] = True
    else:
        print(f"[ACTION_ANALYZER] ❌ Nenhuma palavra-chave de agendamento encontrada")
        print(f"[ACTION_ANALYZER] Mensagem completa (lower): {message_lower}")
    
    # Se detectou agendamento, extrai data/hora
    if actions["needs_scheduling"]:
        
        # Tenta extrair informações de data/hora da mensagem do usuário
        date_patterns = [
            r"(segunda|terça|quarta|quinta|sexta|sábado|domingo)",
            r"(amanhã|amanha|hoje)",
            r"(\d{1,2})[/-](\d{1,2})",
        ]
        
        time_patterns = [
            r"(\d{1,2})[h:](\d{2})",  # "16:00", "16:30"
            r"(\d{1,2})\s*h",         # "16h", "16 h"
            r"às\s+(\d{1,2})[h:]?",    # "às 16h", "às 16"
            r"(\d{1,2})[h:]?$",        # "16" no final
            r"\b(manhã|manha|tarde|noite)\b"
        ]
        
        print(f"[ACTION_ANALYZER] Tentando extrair data/hora da mensagem: '{user_message}'")
        
        for pattern in date_patterns:
            match = re.search(pattern, message_lower)
            if match:
                actions["scheduling_info"]["date"] = match.group(0)
                print(f"[ACTION_ANALYZER] ✅ Data extraída: '{match.group(0)}' (pattern: {pattern})")
                break
        else:
            print(f"[ACTION_ANALYZER] ⚠️ Nenhuma data encontrada nos padrões: {date_patterns}")
        
        for pattern in time_patterns:
            match = re.search(pattern, message_lower)
            if match:
                actions["scheduling_info"]["time"] = match.group(0)
                print(f"[ACTION_ANALYZER] ✅ Hora extraída: '{match.group(0)}' (pattern: {pattern})")
                break
        else:
            print(f"[ACTION_ANALYZER] ⚠️ Nenhuma hora encontrada nos padrões: {time_patterns}")
        
        # Tenta extrair horário específico como "14h", "15h", etc.
        if not actions["scheduling_info"].get("time"):
            hour_match = re.search(r"(\d{1,2})\s*h", message_lower)
            if hour_match:
                actions["scheduling_info"]["time"] = hour_match.group(0)
                print(f"[ACTION_ANALYZER] ✅ Hora extraída (padrão alternativo): '{hour_match.group(0)}'")
        
        actions["scheduling_info"]["text"] = user_message
        print(f"[ACTION_ANALYZER] 📋 Scheduling info final: {json.dumps(actions['scheduling_info'], ensure_ascii=False, indent=2)}")
    
    # Usa DeepSeek para análise mais precisa se necessário
    if actions["needs_scheduling"] or actions["no_meeting_wanted"]:
        analysis_prompt = f"""Analise a seguinte mensagem do USUÁRIO e determine as ações necessárias.

Mensagem do usuário:
{user_message}

Contexto da conversa:
{conversation_text}

Retorne APENAS um JSON válido com:
{{
    "needs_scheduling": true/false,
    "scheduling_date": "data mencionada ou null",
    "scheduling_time": "horário mencionado ou null",
    "no_meeting_wanted": true/false,
    "needs_slack": true/false
}}

JSON:"""
        
        messages = [
            {"role": "system", "content": "Você é um assistente que analisa intenções. Retorne APENAS JSON válido."},
            {"role": "user", "content": analysis_prompt}
        ]
        
        try:
            response = deepseek_client.chat_completion(messages, temperature=0.2, max_tokens=200)
            response = response.strip()
            
            # Remove markdown se houver
            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join(lines[1:-1]) if len(lines) > 2 else response
            
            analysis = json.loads(response)
            
            # Atualiza ações com análise do DeepSeek
            if analysis.get("needs_scheduling"):
                actions["needs_scheduling"] = True
                if analysis.get("scheduling_date"):
                    actions["scheduling_info"]["date"] = analysis["scheduling_date"]
                if analysis.get("scheduling_time"):
                    actions["scheduling_info"]["time"] = analysis["scheduling_time"]
            
            if analysis.get("no_meeting_wanted"):
                actions["no_meeting_wanted"] = True
                actions["needs_slack_notification"] = True
            
        except Exception as e:
            print(f"[ACTION] Erro na análise detalhada: {e}")
            # Mantém detecção básica
    
    print(f"[ACTION] Ações detectadas: {[k for k, v in actions.items() if v and k != 'scheduling_info']}")
    
    return actions
