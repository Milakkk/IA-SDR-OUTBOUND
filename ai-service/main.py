import os
import time as time_module
import json
import csv
from typing import Optional, Dict, Any, List
import random
from datetime import datetime, timedelta, time

from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from pathlib import Path
from dateutil import parser as dateparser
from dateutil import tz

import scheduler as gcal_scheduler
import crm
import slack_notify
import re
from zoneinfo import ZoneInfo
import requests
import deepseek_client
import data_extractor
import action_analyzer
import conversation_persistence
import threading

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

load_dotenv(dotenv_path=str(Path(__file__).resolve().parents[1] / '.env'))

MODEL = os.getenv("MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")
OFFLINE_MODE = os.getenv("OFFLINE_MODE", "").lower() == "true"

app = FastAPI(title="IA WhatsApp SDR")

# Lock global para controle de concorrência e acesso a dados compartilhados
_state_lock = threading.Lock()

# Carrega system prompt
def _load_system_prompt() -> str:
    """Carrega o system prompt do arquivo assistant.system.txt"""
    prompt_path = Path(__file__).resolve().parent / "assistant.system.txt"
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            prompt = f.read().strip()
            # Remove referências a tool calls que não existem mais
            prompt = prompt.replace("chame \"query_product_knowledge\"", "use as informações sobre produtos")
            prompt = prompt.replace("chame \"route_seller\"", "identifique o vendedor correto")
            return prompt
    except Exception as e:
        print(f"[ERROR] Erro ao carregar system prompt: {e}")
        return "Você é Eduardo, um SDR da Silicon que atende leads via WhatsApp."

# Carrega system prompt na inicialização
system_prompt = _load_system_prompt()
print(f"[INIT] System prompt carregado ({len(system_prompt)} caracteres)")

# Cliente OpenAI (apenas para transcrição)
client: Optional[OpenAI] = None

# Histórico de conversa por contato (DeepSeek)
# NOTA: Histórico apenas em memória - não persiste entre reinicializações do servidor
# Cada contato mantém seu histórico apenas durante a sessão atual
conversation_history: Dict[str, List[Dict[str, str]]] = {}  # contact_id -> [{"role": "...", "content": "..."}]

# Configurações de fallback
DEEPSEEK_MAX_ATTEMPTS = 2
FALLBACK_REPLY = "Olá! Desculpe, estou com dificuldades técnicas no momento. Pode repetir sua mensagem?"

# Estado por contato
first_greetings_done: Dict[str, bool] = {}
seller_by_contact: Dict[str, Dict[str, Any]] = {}  # contact_id -> seller (isolado por contato)
run_lock_by_contact: Dict[str, bool] = {}
salesforce_leads_by_contact: Dict[str, Dict[str, str]] = {}
pipedrive_deals_by_contact: Dict[str, Dict[str, str]] = {}  # contact_id -> {"id": deal_id, "link": pipedrive_link}
lead_data_by_contact: Dict[str, Dict[str, Any]] = {}  # Dados extraídos por contato
calendar_events_by_contact: Dict[str, Dict[str, Any]] = {}

# Estado simples de agendamento por contato
scheduling_sessions: Dict[str, Dict[str, Any]] = {}
# Estado de captura de lead por contato
lead_sessions: Dict[str, Dict[str, Any]] = {}

def _load_persisted_state() -> None:
    global conversation_history
    global lead_data_by_contact
    global scheduling_sessions
    global seller_by_contact
    global salesforce_leads_by_contact
    global pipedrive_deals_by_contact
    global calendar_events_by_contact
    try:
        st = conversation_persistence.load_conversation_state() or {}
        with _state_lock:
            conversation_history = st.get("conversation_history") or {}
            lead_data_by_contact = st.get("lead_data_by_contact") or {}
            scheduling_sessions = st.get("scheduling_sessions") or {}
            seller_by_contact = st.get("seller_by_contact") or {}
            salesforce_leads_by_contact = st.get("salesforce_leads_by_contact") or {}
            pipedrive_deals_by_contact = st.get("pipedrive_deals_by_contact") or {}
            calendar_events_by_contact = st.get("calendar_events_by_contact") or {}
    except Exception:
        return

def _save_persisted_state() -> None:
    try:
        # Pega snapshots sob lock para evitar inconsistência durante o I/O
        with _state_lock:
            st_conv = conversation_history.copy()
            st_lead = lead_data_by_contact.copy()
            st_sched = scheduling_sessions.copy()
            st_sell = seller_by_contact.copy()
            st_sf = salesforce_leads_by_contact.copy()
            st_pd = pipedrive_deals_by_contact.copy()
            st_cal = calendar_events_by_contact.copy()

        conversation_persistence.save_conversation_state(
            conversation_history=st_conv,
            lead_data_by_contact=st_lead,
            scheduling_sessions=st_sched,
            seller_by_contact=st_sell,
            salesforce_leads_by_contact=st_sf,
            pipedrive_deals_by_contact=st_pd,
            calendar_events_by_contact=st_cal
        )
    except Exception as e:
        print(f"[ERROR] Erro ao salvar estado: {e}")

_load_persisted_state()

# Carrega configuração de agendas
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "scheduling.config.json")
SCHED_CFG: Dict[str, Any] = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        SCHED_CFG = json.load(f)
DEFAULT_CFG = SCHED_CFG.get("default", {
    "calendarId": "seller-default@example.com",
    "timezone": "America/Sao_Paulo",
    "meetingDurationMinutes": 30,
    "workStart": "09:00",
    "workEnd": "18:00",
})

# Carrega configuração de roteamento e perguntas por setor
ROUTING_CFG_PATH = os.path.join(os.path.dirname(__file__), "routing.config.json")
ROUTE_CFG: Dict[str, Any] = {}
if os.path.exists(ROUTING_CFG_PATH):
    with open(ROUTING_CFG_PATH, "r", encoding="utf-8") as f:
        ROUTE_CFG = json.load(f)

def _extract_ddd(from_id: Optional[str]) -> Optional[str]:
    # Extrai DDD do telefone brasileiro, assumindo formato '55<DDD><número>'
    if not from_id:
        return None
    m = re.search(r"55(\d{2})\d+", from_id)
    return m.group(1) if m else None

def _digits_only(s: Optional[str]) -> str:
    return re.sub(r"\D+", "", str(s or ""))

def _preload_contact_context(contact_id: str) -> None:
    try:
        cid = _digits_only(contact_id)
        if not cid:
            return
        if cid in lead_data_by_contact and isinstance(lead_data_by_contact.get(cid), dict) and lead_data_by_contact[cid]:
            return

        project_root = Path(__file__).resolve().parents[1]
        follow_ups_path = project_root / "outbound_data" / "follow_ups.json"
        base_csv_path = project_root / "outbound_data" / "contatos_base.csv"

        ctx: Dict[str, Any] = {}

        fu = None
        if follow_ups_path.exists():
            try:
                with open(follow_ups_path, "r", encoding="utf-8") as f:
                    fu_all = json.load(f) or {}
                fu = fu_all.get(cid) or fu_all.get(str(cid))
            except Exception:
                fu = None

        if isinstance(fu, dict):
            if fu.get("empresa"):
                ctx["empresa"] = fu.get("empresa")
            if fu.get("contato"):
                ctx["nome"] = fu.get("contato")
            if fu.get("email"):
                ctx["email"] = fu.get("email")
            if fu.get("cidade"):
                ctx["cidade"] = fu.get("cidade")
            if fu.get("estado"):
                ctx["estado"] = fu.get("estado")
            if fu.get("cnpj"):
                ctx["cnpj"] = fu.get("cnpj")
            setor = fu.get("setor") or fu.get("sector")
            if setor:
                ctx["setor"] = setor

        if base_csv_path.exists():
            try:
                with open(base_csv_path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if _digits_only(row.get("telefone")) != cid:
                            continue
                        if row.get("empresa") and not ctx.get("empresa"):
                            ctx["empresa"] = row.get("empresa")
                        if row.get("contato") and not ctx.get("nome"):
                            ctx["nome"] = row.get("contato")
                        if row.get("email") and not ctx.get("email"):
                            ctx["email"] = row.get("email")
                        if row.get("cidade") and not ctx.get("cidade"):
                            ctx["cidade"] = row.get("cidade")
                        if row.get("estado") and not ctx.get("estado"):
                            ctx["estado"] = row.get("estado")
                        if row.get("cnpj") and not ctx.get("cnpj"):
                            ctx["cnpj"] = row.get("cnpj")
                        break
            except Exception:
                pass

        if ctx:
            ctx["telefone"] = cid
            ctx["phone"] = cid
            if not ctx.get("setor"):
                ctx["setor"] = "grow"
            sector = ctx.get("setor") or ctx.get("sector") or "grow"
            lead_data_by_contact[cid] = {"sector": sector, "data": ctx}
    except Exception:
        return

def _parse_city_state(text: Optional[str]) -> Dict[str, Optional[str]]:
    """Extrai cidade e UF de entradas como 'Curitiba', 'Curitiba-PR', 'Curitiba/PR' ou 'Curitiba, PR'."""
    city = None
    state = None
    t = (text or "").strip()
    if not t:
        return {"city": None, "state": None}
    m = re.match(r"\s*([A-Za-zÀ-ÿ'´`\- ]+)\s*[-/,]\s*([A-Za-z]{2})\s*$", t)
    if m:
        city = m.group(1).strip()
        state = m.group(2).lower()
    else:
        city = t
    return {"city": city, "state": state}

def _extract_fields(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    low = t.lower()
    out: Dict[str, Any] = {}
    m_name = re.search(r"(?i)meu\s+nome\s+e\s+([a-zà-ÿ][a-zà-ÿ\s\-']{1,50})", t) or re.search(r"(?i)meu\s+nome\s+é\s+([a-zà-ÿ][a-zà-ÿ\s\-']{1,50})", t) or re.search(r"(?i)me\s+chamo\s+([a-zà-ÿ][a-zà-ÿ\s\-']{1,50})", t)
    if m_name:
        name = m_name.group(1).strip()
        name = re.split(r"[\.,;\n]", name)[0].strip()
        if name:
            out["Contato"] = name
    cs = _parse_city_state(t)
    if cs.get("city"):
        out["city"] = cs.get("city")
    if cs.get("state"):
        out["state"] = cs.get("state")
    if any(k in low for k in ["privado", "empresa privada"]):
        out["tipo_cliente"] = "privado"
    elif any(k in low for k in ["público", "publico", "prefeitura", "órgão", "orgao"]):
        out["tipo_cliente"] = "público"
    places = [
        "galpão", "galpao", "pátio", "patio", "estacionamento",
        "fábrica", "fabrica", "escritório", "escritorio", "sede",
        "linha de produção", "linha de producao", "depósito", "deposito",
        "armazém", "armazem"
    ]
    for w in places:
        if w in low:
            out["tipo_lugar"] = w
            break
    segments = ["industrial", "comércio", "comercio", "logística", "logistica", "saúde", "saude", "educação", "educacao", "agrícola", "agricola"]
    for s in segments:
        if s in low:
            out["segmento"] = s
            break
    if any(k in low for k in ["projeto luminotécnico", "projeto luminotecnico", "projeto"]):
        val = "sim"
        if any(n in low for n in ["não", "nao", "ainda não", "ainda nao", "sem"]):
            val = "não"
        out["projeto_luminotecnico"] = val
    m = re.search(r"(\d{2,5})\s*(m2|m²|m\^2|metros|m)", low)
    if m:
        out["tamanho"] = m.group(0)
    if any(k in low for k in ["orçamento", "orcamento", "trocar a iluminação", "trocar a iluminacao", "troca de iluminacao", "iluminação", "iluminacao"]):
        out["need"] = t
    return {k: v for k, v in out.items() if v}

def _detect_sector(text: str) -> Optional[str]:
    s_defs = (ROUTE_CFG.get("sectors") or {})
    low = text.lower()
    for sector, meta in s_defs.items():
        for kw in meta.get("keywords", []):
            k = (kw or "").lower()
            if k == "ip":
                if re.search(r"\bip\b", low):
                    return sector
            elif k and k in low:
                return sector
    return None

def _questions_for_sector(sector: str):
    return ((ROUTE_CFG.get("sectors") or {}).get(sector) or {}).get("questions", [])

def _crm_minimum_questions():
    return [
        "Qual é seu nome completo?",
        "Qual é seu e-mail?",
        "Qual é seu telefone?",
        "Qual é o nome da empresa?",
    ]

def _prepare_salesforce_data(data: Dict[str, Any], sector: Optional[str] = None) -> Dict[str, Any]:
    """
    Prepara todos os dados coletados para o Salesforce, mapeando por setor.
    """
    lead_data = {
        "contact_name": data.get("Contato") or data.get("nome") or None,
        "nome": data.get("Contato") or data.get("nome") or None,
        "email": data.get("email"),
        "phone": data.get("phone") or data.get("telefone"),
        "mobile": data.get("celular") or data.get("whatsapp"),
        "sector": sector,
        "company": data.get("empresa") or data.get("Company") or None,
        "empresa": data.get("empresa") or data.get("Company") or None,
        "segment": data.get("segmento"),
        "segmento": data.get("segmento"),
        "source": data.get("source") or "WhatsApp",
        "city": data.get("city") or data.get("cidade"),
        "cidade": data.get("city") or data.get("cidade"),
        "state": data.get("state") or data.get("estado") or data.get("uf"),
        "estado": data.get("state") or data.get("estado") or data.get("uf"),
        "uf": data.get("state") or data.get("estado") or data.get("uf"),
        "cep": data.get("cep") or data.get("postal_code"),
        "endereco": data.get("endereco") or data.get("rua") or data.get("street") or data.get("localizacao"),
        "rua": data.get("endereco") or data.get("rua") or data.get("street"),
        "cargo": data.get("cargo") or data.get("title"),
        "title": data.get("cargo") or data.get("title"),
        "website": data.get("website") or data.get("site"),
        "cnpj": data.get("cnpj") or data.get("CNPJ"),
    }
    
    # Adiciona dados específicos por setor
    if sector == "industrial":
        lead_data.update({
            "tipo_lugar": data.get("tipo_lugar") or data.get("tipo_local") or data.get("tipo_de_lugar"),
            "tipo_local": data.get("tipo_lugar") or data.get("tipo_local") or data.get("tipo_de_lugar"),
            "tamanho": data.get("tamanho") or data.get("area") or data.get("dimensoes") or data.get("m2") or data.get("m²"),
            "area": data.get("area") or data.get("tamanho") or data.get("dimensoes") or data.get("m2") or data.get("m²"),
            "dimensoes": data.get("dimensoes") or data.get("tamanho") or data.get("area"),
            "m2": data.get("m2") or data.get("m²") or data.get("area"),
            "m²": data.get("m²") or data.get("m2") or data.get("area"),
            "projeto_luminotecnico": data.get("projeto_luminotecnico") or data.get("projeto") or data.get("ja_tem_projeto"),
            "publico_privado": data.get("publico_privado") or data.get("publico") or data.get("privado"),
            "nivel_iluminacao": data.get("nivel_iluminacao") or data.get("iluminancia") or data.get("lux"),
            "iluminancia": data.get("iluminancia") or data.get("nivel_iluminacao") or data.get("lux"),
        })
    elif sector == "esportivo":
        lead_data.update({
            "tipo_local": data.get("tipo_local") or data.get("tipo_lugar") or data.get("estadio") or data.get("quadra") or data.get("ginasio") or data.get("arena"),
            "dimensoes": data.get("dimensoes") or data.get("tamanho") or data.get("area") or data.get("100m x 70m"),
            "iluminancia": data.get("iluminancia") or data.get("nivel_iluminacao") or data.get("lux"),
            "projeto_luminotecnico": data.get("projeto_luminotecnico") or data.get("projeto") or data.get("ja_tem_projeto"),
            "segmento": data.get("segmento") or data.get("clube") or data.get("prefeitura") or data.get("privado"),
        })
    elif sector == "cidades":
        lead_data.update({
            "cnpj": data.get("cnpj"),
            "produto": data.get("produto") or data.get("solar") or data.get("led") or data.get("bateria") or data.get("iluminacao"),
            "localizacao": data.get("localizacao") or data.get("endereco_projeto") or data.get("endereco"),
            "projeto_orcamento": data.get("projeto_orcamento") or data.get("projeto") or data.get("orcamento"),
            "licitacao": data.get("licitacao"),
        })
    elif sector == "grow":
        lead_data.update({
            "cnpj": data.get("cnpj") or data.get("CNPJ"),
            "estrutura": data.get("estrutura") or data.get("estufa") or data.get("casa_vegetacao"),
            "cultivo": data.get("cultivo") or data.get("tipo_cultivo"),
            "muda": data.get("muda") or data.get("mudas"),
            "area": data.get("area") or data.get("dimensoes") or data.get("m2") or data.get("m²"),
        })
    
    # Remove None values
    return {k: v for k, v in lead_data.items() if v is not None}


def _route_seller(ddd: Optional[str], state: Optional[str], sector: Optional[str]) -> Dict[str, Any]:
    routing = ROUTE_CFG.get("routing", {})
    rules = routing.get("rules", [])
    ddd_low = (ddd or "").lower()
    state_low = (state or "").lower()
    sector_low = (sector or "").lower()

    # Setores não-industriais: regras específicas (sem DDD/estado) têm prioridade
    if sector_low and sector_low != "industrial":
        for r in rules:
            sectors = [sec.lower() for sec in r.get("sectors", [])]
            ddds = [d.lower() for d in r.get("ddds", [])]
            states = [s.lower() for s in r.get("states", [])]
            if (sector_low in sectors) and (not ddds and not states):
                return r.get("seller", {})

    # Passo 1: correspondência exata por DDD para industrial
    for r in rules:
        sectors = [sec.lower() for sec in r.get("sectors", [])]
        ddds = [d.lower() for d in r.get("ddds", [])]
        if sector_low == "industrial" and ddd_low and ddd_low in ddds and (sector_low in sectors or not sectors):
            return r.get("seller", {})

    # Passo 2: correspondência por estado apenas quando a regra não especifica DDD
    for r in rules:
        sectors = [sec.lower() for sec in r.get("sectors", [])]
        ddds = [d.lower() for d in r.get("ddds", [])]
        states = [s.lower() for s in r.get("states", [])]
        if sector_low == "industrial" and not ddds and state_low and state_low in states and (sector_low in sectors or not sectors):
            return r.get("seller", {})

    return routing.get("defaultSeller", {})

def _summarize_data(data: Dict[str, Any]) -> str:
    parts = []
    for k, v in data.items():
        if v:
            parts.append(f"{k}: {v}")
    return "\n".join(parts)

def _build_slack_message(
    summary: str,
    scheduling_success: bool = False,
    created_event: Optional[Dict] = None,
    start_dt_scheduled: Optional[datetime] = None,
    reuniao_urgente: bool = False,
    cliente_nao_quer_reuniao: bool = False,
    sf_link: str = "",
    pipedrive_link: str = ""
) -> str:
    """
    Monta uma mensagem única e completa para o Slack com todas as informações.
    
    Args:
        summary: Resumo da conversa/lead
        scheduling_success: Se a reunião foi agendada com sucesso
        created_event: Dados do evento criado no Google Calendar
        start_dt_scheduled: Data/hora da reunião agendada
        reuniao_urgente: Se é uma reunião urgente
        cliente_nao_quer_reuniao: Se o cliente não quis reunião
        sf_link: Link do lead no Salesforce
    
    Returns:
        Mensagem formatada para o Slack
    """
    msg_parts = []
    
    # Cabeçalho
    if reuniao_urgente:
        msg_parts.append("🚨 *REUNIÃO URGENTE - RESPOSTA RÁPIDA NECESSÁRIA* 🚨")
    elif scheduling_success and created_event and start_dt_scheduled:
        when_txt = start_dt_scheduled.strftime('%d/%m/%Y às %H:%M')
        msg_parts.append(f"✅ *Reunião Agendada: {when_txt}*")
    elif cliente_nao_quer_reuniao:
        msg_parts.append("📋 *Novo Lead - Sem Agendamento*")
    else:
        msg_parts.append("📋 *Novo Lead via WhatsApp*")
    
    msg_parts.append("")  # Linha em branco
    
    # Resumo da conversa
    msg_parts.append("*Resumo da Conversa:*")
    msg_parts.append(summary)
    msg_parts.append("")  # Linha em branco
    
    # Status do agendamento
    if reuniao_urgente:
        msg_parts.append("⚠️ Cliente precisa de resposta rápida. Por favor, entre em contato por telefone ou WhatsApp *O QUANTO ANTES*.")
    elif scheduling_success and created_event and start_dt_scheduled:
        msg_parts.append("✅ Reunião confirmada e agendada no calendário.")
    elif cliente_nao_quer_reuniao:
        msg_parts.append("ℹ️ Cliente não quis agendar reunião, mas precisa de orçamento. Por favor, entre em contato por telefone ou WhatsApp.")
    else:
        msg_parts.append("ℹ️ Por favor, entre em contato por telefone ou WhatsApp.")
    
    msg_parts.append("")  # Linha em branco
    
    # Links
    links_section = []
    
    # Link da reunião (se houver)
    if scheduling_success and created_event:
        calendar_link = created_event.get('htmlLink', '')
        if calendar_link:
            links_section.append(f"📅 *Link da Reunião:* {calendar_link}")
    
    # Link do Salesforce (se houver e não for cidades)
    # Para cidades, NÃO mostra link do Salesforce, apenas Pipedrive
    if sf_link and not pipedrive_link:
        links_section.append(f"📊 *Link do Salesforce:* {sf_link}")
    
    # Link do Pipedrive (se houver - para cidades)
    # Para cidades, mostra APENAS o link do Pipedrive
    if pipedrive_link:
        links_section.append(f"📊 *Link do Pipedrive:* {pipedrive_link}")
    
    if links_section:
        msg_parts.append("---")
        msg_parts.extend(links_section)
    
    return "\n".join(msg_parts)

def _build_sf_lead_link(lead_id: Optional[str]) -> str:
    if not lead_id:
        return ""
    host = os.getenv("SALESFORCE_INSTANCE_HOST") or os.getenv("SALESFORCE_INSTANCE_URL") or "login.salesforce.com"
    host = host.replace("https://", "").replace("http://", "").strip("/")
    return f"https://{host}/lightning/r/Lead/{lead_id}/view"

def _start_lead(contact_id: str, text: str, from_id: Optional[str]) -> str:
    sector = _detect_sector(text)
    seed = _extract_fields(text)
    lead_sessions[contact_id] = {
        "stage": "collect",
        "sector": sector,
        "data": {
            "source": "WhatsApp",
            "from": from_id,
            **seed,
        },
        "pending_questions": [],
    }
    lead_data_by_contact[contact_id] = {"sector": sector, "data": lead_sessions[contact_id]["data"]}
    sess = lead_sessions[contact_id]
    data = sess["data"]
    q = None
    if not data.get("Contato"):
        q = "Qual é seu nome?"
    elif not (data.get("city") or data.get("localizacao")):
        q = "Qual a cidade?"
    elif sector:
        for pq in _questions_for_sector(sector):
            if not data.get(pq):
                q = pq
                break
    else:
        q = "Qual o tipo de lugar?"
    if q:
        sess["pending_questions"] = [q]
        sess["last_question"] = q
    intro = "Oi! Sou o Eduardo, tudo bem? "
    ack = ""
    if _is_lead_intent(text):
        ack = "Vi seu pedido de orçamento. "
    return f"{intro}{ack}{q}" if q else f"{intro}{ack}"

def _continue_lead(contact_id: str, text: str) -> str:
    sess = lead_sessions.get(contact_id)
    if not sess:
        return _start_lead(contact_id, text, None)
    data = sess.get("data", {})
    last_q = sess.get("last_question")

    def _is_confusion(t: str) -> bool:
        low = (t or "").lower().strip()
        return (
            "como assim" in low
            or "não entendi" in low
            or "nao entendi" in low
            or "pode explicar" in low
            or "explica melhor" in low
            or "o que é" in low
            or "oq é" in low
            or "o que significa" in low
        )

    def _clarify_for_question(q: str) -> str:
        qlow = (q or "").lower()
        if "dimensões" in qlow and "iluminância" in qlow:
            return (
                "Claro! Por dimensões, me refiro ao tamanho do local (ex.: comprimento e largura do campo, altura das luminárias). "
                "E por exigências de iluminância, é o nível de luz em lux exigido (ex.: treino, jogo oficial, transmissão TV). "
                "Se não tiver os números agora, posso estimar e ajustar depois. Pode me dizer as dimensões aproximadas ou o nível desejado?"
            )
        if "projeto luminotécnico" in qlow:
            return (
                "Sem problema! Projeto luminotécnico é um estudo técnico que calcula quantas luminárias usar, potência e níveis de luz. "
                "Se ainda for prévio, podemos avançar com estimativa e detalhar depois. Você tem algum documento ou só uma ideia inicial?"
            )
        return "Claro! Posso explicar melhor. Me traga um pouco mais de detalhe sobre isso para eu registrar direitinho."

    def _sector_from_text(t: str) -> Optional[str]:
        s = _detect_sector(t)
        if s:
            return s
        low = (t or "").lower()
        # Prioridade: verificar palavras-chave específicas primeiro
        if any(k in low for k in ["estádio", "estadio", "arena", "quadra", "ginásio", "ginasio", "alta eficiência", "alta eficiencia"]):
            return "esportivo"
        if any(k in low for k in ["estufa", "casa de vegetação", "casa de vegetacao", "cultivo", "grow", "hortaliças", "hortalicas", "eucalipto", "pinus", "café", "cafe", "muda", "mudas", "microverde"]):
            return "grow"
        # Cidades: precisa ter palavras-chave de licitação/prefeitura (CNPJ sozinho não é suficiente)
        if any(k in low for k in ["prefeitura", "município", "municipio", "licitação", "licitacao", "iluminação pública", "iluminacao publica", "via pública", "via publica", "rua", " ip "]):
            return "cidades"
        # Industrial: solar, empresa privada, etc (tem prioridade sobre cidades se não houver palavras-chave de licitação)
        # IMPORTANTE: "solar", "fotovoltaica", "usina solar" são industrial, NÃO iluminação
        if any(k in low for k in ["usina solar", "fotovoltaica", "energia solar", "solar fotovoltaica", "bess"]):
            return "industrial"
        if any(k in low for k in ["empresa", "indústria", "industria", "fábrica", "fabrica", "galpão", "galpao", "pátio", "patio", "estacionamento", "estacionamentos", "iluminação", "iluminacao"]):
            return "industrial"
        return None

    def _map_answer(q: str, ans: str) -> None:
        data[q] = ans
        low = q.lower()
        if "nome" in low:
            data["Contato"] = ans
        elif "e-mail" in low or "email" in low:
            data["email"] = ans
        elif "telefone" in low:
            data["phone"] = ans
        elif "empresa" in low:
            data["empresa"] = ans
        elif "cidade" in low or "localização" in low or "localizacao" in low or "município" in low or "municipio" in low:
            cs = _parse_city_state(ans)
            data["city"] = cs.get("city")
            data["state"] = cs.get("state")
        elif "segmento" in low:
            data["segmento"] = ans
        elif "público" in low or "publico" in low:
            data["tipo_cliente"] = ans
        elif "tipo de lugar" in low or "tipo de local" in low or "instalação" in low or "instalacao" in low:
            data["tipo_lugar"] = ans
        elif "projeto luminotécnico" in low or "projeto luminotecnico" in low:
            a_low = (ans or "").lower()
            val = "sim"
            if any(n in a_low for n in ["não", "nao", "sem", "ainda não", "ainda nao"]):
                val = "não"
            data["projeto_luminotecnico"] = val
        elif "tamanho" in low or "dimensões" in low or "dimensoes" in low:
            a_low = (ans or "").lower()
            val = ans
            if any(n in a_low for n in ["não sei", "nao sei", "não tenho", "nao tenho", "desconhecido", "não informado", "nao informado"]):
                val = "desconhecido"
            data["tamanho"] = val
        elif "cnpj" in low:
            data["cnpj"] = ans
        elif "produto" in low:
            data["produto"] = ans
        elif "estrutura" in low:
            data["estrutura"] = ans
        elif "cultivo" in low:
            data["cultivo"] = ans
        elif "muda" in low:
            data["muda_sem_cultivo"] = ans
        if _is_lead_intent(ans) and not data.get("need"):
            data["need"] = ans

    if last_q:
        if _is_confusion(text):
            hint = _clarify_for_question(last_q)
            return f"{hint}"
        _map_answer(last_q, text)
        extra = _extract_fields(text)
        for k, v in extra.items():
            if not data.get(k):
                data[k] = v
        
        # Se a última pergunta foi sobre interesse, salva a resposta
        if "interesse" in (last_q or "").lower() or "como posso te ajudar" in (last_q or "").lower():
            data["interesse_cliente"] = text
            data["interesse_coletado"] = True
        
        lead_data_by_contact[contact_id] = {"sector": sess.get("sector"), "data": data}
        def _is_greeting_text(t: str) -> bool:
            low = (t or "").strip().lower()
            return low in ["oi", "ola", "olá", "bom dia", "boa tarde", "boa noite", "hey", "eae", "salve"]
        if "nome" in (last_q or "").lower():
            if _is_greeting_text(text):
                data.pop("Contato", None)
                q = "Qual é seu nome?"
                sess["last_question"] = q
                sess["pending_questions"] = [q]
                return q

    if not data.get("Contato"):
        q = "Qual é seu nome?"
        sess["last_question"] = q
        sess["pending_questions"] = [q]
        return q

    if not (data.get("city") or data.get("localizacao")):
        q = "Qual a cidade?"
        sess["last_question"] = q
        sess["pending_questions"] = [q]
        return f"Perfeito! E {q}"
    
    # Após coletar nome e cidade, faz apresentação da Silicon e pergunta interesse
    if data.get("Contato") and (data.get("city") or data.get("localizacao")) and not data.get("interesse_coletado"):
        nome = data.get("Contato")
        cidade = data.get("city") or data.get("localizacao")
        sess["data"]["interesse_coletado"] = True  # Marca que já fez apresentação
        apresentacao = f"Prazer, {nome}! A Silicon é especialista em iluminação LED profissional para grandes projetos, como indústrias, estádios e iluminação pública, além de usinas solares fotovoltaicas de grande porte. Já iluminamos vários estádios e grandes indústrias no Brasil. Qual seria seu interesse na Silicon? Como posso te ajudar?"
        sess["last_question"] = "Qual seria seu interesse na Silicon? Como posso te ajudar?"
        sess["pending_questions"] = ["Qual seria seu interesse na Silicon? Como posso te ajudar?"]
        return apresentacao

    need_q = "Me conta, por favor, em poucas palavras o que você precisa?"
    low_now = (text or "").lower()
    price_intent_now = any(k in low_now for k in ["preço", "preco", "orçamento", "orcamento", "cotação", "cotacao"])
    schedule_intent_now = any(k in low_now for k in ["agendar", "reunião", "reuniao", "horário", "horario", "sugerir", "sugira", "sugestao", "sugestão"])

    if not sess.get("sector"):
        s_detected = _sector_from_text(text)
        if s_detected:
            sess["sector"] = s_detected

    sector = sess.get("sector")

    required = []
    if not (data.get("city") or data.get("localizacao")):
        required.append("Qual a cidade?")
    # Dados mínimos para CRM
    if not data.get("Contato"):
        required.append("Qual é seu nome completo?")
    if not data.get("email"):
        required.append("Qual é seu e-mail?")
    # Telefone: não pergunta obrigatoriamente, pois já está falando pelo WhatsApp
    # Apenas confirma se é o mesmo número se não tiver coletado ainda
    if not data.get("phone") and not data.get("telefone"):
        # Usa o contact_id como telefone (já está no WhatsApp)
        contact_id_clean = contact_id.replace("55", "").replace("+", "").replace("-", "").replace(" ", "")
        if contact_id_clean and len(contact_id_clean) >= 10:
            data["phone"] = contact_id_clean
            data["telefone"] = contact_id_clean
            print(f"[FLUXO] Telefone extraído do contact_id: {contact_id_clean}")
    if not data.get("empresa"):
        required.append("Qual é o nome da empresa?")
    if sector:
        for q in _questions_for_sector(sector):
            # 1. Check if the question string itself is a key (legacy/direct answer)
            if data.get(q):
                continue

            # 2. Check if the underlying FIELD is already present
            low = q.lower()
            
            # Empresas
            if ("nome da empresa" in low or "empresa" in low):
                print(f"[DEBUG] Checking empresa: q='{q}', data.get('empresa')='{data.get('empresa')}'")
                if data.get("empresa"):
                    print(f"[DEBUG] SKIPPING {q} because empresa is present")
                    continue
            
            # Cidade/Localização
            if ("cidade" in low or "localizacao" in low) and (data.get("city") or data.get("localizacao") or data.get("municipio")):
                continue
                
            # Nome (Contato) - Cuidado para não confundir com nome da empresa
            if "nome" in low and "empresa" not in low and data.get("Contato"):
                continue
                
            # Email
            if ("email" in low or "e-mail" in low) and data.get("email"):
                 continue
                 
            # Telefone
            if ("telefone" in low or "whatsapp" in low) and (data.get("phone") or data.get("telefone")):
                 continue
            
            # Campos específicos do setor GROW
            if ("cultivo" in low) and (data.get("cultivo") or data.get("tipo_cultivo")):
                 continue
            if ("estrutura" in low or "estufa" in low or "vegetação" in low) and (data.get("estrutura") or data.get("estufa") or data.get("casa_vegetacao")):
                 continue
            if ("muda" in low) and (data.get("muda") or data.get("mudas") or data.get("muda_sem_cultivo")):
                 continue
            if ("área" in low or "area" in low or "tamanho" in low) and (data.get("area") or data.get("tamanho") or data.get("dimensoes") or data.get("m2") or data.get("m²")):
                 continue
            
            # Se nenhum campo equivalente foi encontrado, a pergunta é necessária
            required.append(q)

    if schedule_intent_now and required:
        q = required[0]
        sess["last_question"] = q
        sess["pending_questions"] = [q]
        pref = (
            "Vamos agendar sim. Antes, vou te fazer algumas perguntas rápidas para prosseguir com o agendamento: "
            if len(required) > 1
            else "Vamos agendar sim. Antes, preciso de uma informação rápida para prosseguir com o agendamento: "
        )
        pi = _product_info_snippet(text) if _is_product_inquiry(text) else ""
        return f"{(pi + ' ') if pi else ''}{pref}{q}"

    if sector == "cidades":
        if (re.search(r"\bip\b", low_now) or any(k in low_now for k in ["iluminação pública", "iluminacao publica", "via pública", "via publica", "rua"])):
            pass

    if price_intent_now and sector == "industrial" and any(x in low_now for x in ["solar", "fotovolta", "usina", "gd", "fotovoltaica"]):
        # Antes de agendar, coleta mínimos do CRM se faltarem
        q = None
        if not data.get("Contato"):
            q = "Qual é seu nome completo?"
        elif not data.get("email"):
            q = "Qual é seu e-mail?"
        # Telefone: não pergunta, usa o contact_id (já está no WhatsApp)
        elif not data.get("phone") and not data.get("telefone"):
            contact_id_clean = contact_id.replace("55", "").replace("+", "").replace("-", "").replace(" ", "")
            if contact_id_clean and len(contact_id_clean) >= 10:
                data["phone"] = contact_id_clean
                data["telefone"] = contact_id_clean
                print(f"[FLUXO] Telefone extraído do contact_id: {contact_id_clean}")
        if not data.get("empresa"):
            q = "Qual é o nome da empresa?"
        if q:
            sess["last_question"] = q
            sess["pending_questions"] = [q]
            return q
        return _start_scheduling(contact_id, sector)

    if sector:
        for q in _questions_for_sector(sector):
            if not data.get(q):
                sess["last_question"] = q
                sess["pending_questions"] = [q]
                pi = _product_info_snippet(text) if _is_product_inquiry(text) else ""
                return f"{(pi + ' ') if pi else ''}{q}"

    if (sess.get("last_question") or "").strip().lower() == need_q.lower():
        data["need"] = text
        low_need = (text or "").lower()
        price_intent = any(k in low_need for k in ["preço", "preco", "orçamento", "orcamento", "cotação", "cotacao"])
        project_intent = any(k in low_need for k in ["projeto", "luminotécnico", "luminotecnico"])
        lack_info_now = any(k in low_need for k in ["não tenho", "nao tenho", "não sei", "nao sei", "sem informação", "sem informacao", "sem dados"]) 
        info = _product_info_snippet(text) if _is_product_inquiry(text) else ""
        if price_intent or project_intent:
            req = []
            if not (data.get("city") or data.get("localizacao")):
                req.append("Qual a cidade?")
            # CRM mínimos
            if not data.get("Contato"):
                req.append("Qual é seu nome completo?")
            if not data.get("email"):
                req.append("Qual é seu e-mail?")
            # Telefone: não pergunta, usa o contact_id (já está no WhatsApp)
            if not data.get("phone") and not data.get("telefone"):
                contact_id_clean = contact_id.replace("55", "").replace("+", "").replace("-", "").replace(" ", "")
                if contact_id_clean and len(contact_id_clean) >= 10:
                    data["phone"] = contact_id_clean
                    data["telefone"] = contact_id_clean
                    print(f"[FLUXO] Telefone extraído do contact_id: {contact_id_clean}")
            if not data.get("empresa"):
                req.append("Qual é o nome da empresa?")
            if sector:
                for q in _questions_for_sector(sector):
                    if not data.get(q):
                        req.append(q)
            else:
                req.append("Qual o tipo de lugar?")
            if req:
                q = req[0]
                sess["last_question"] = q
                sess["pending_questions"] = [q]
                return f"{info} {q}" if info else q
            return _start_scheduling(contact_id, sector)
        q = "Quer falar com nosso vendedor? Posso sugerir um horário pra você."
        sess["last_question"] = q
        sess["pending_questions"] = [q]
        return f"Perfeito! {info} {q}"

    if sector and not required:
        ddd = _extract_ddd(data.get("from"))
        state = data.get("state")
        seller = _route_seller(ddd, state, sector)
        sector_low = (sector or "").lower()
        pd_owner = seller.get("pipedrive_user_id")
        if sector_low == "cidades":
            try:
                crm.create_person_and_deal_pipedrive({
                    "contact_name": data.get("Contato") or data.get("segmento") or None,
                    "email": data.get("email"),
                    "phone": data.get("phone"),
                    "sector": sector,
                    "company": data.get("empresa") or None,
                    "source": data.get("source"),
                }, owner_id=pd_owner) if pd_owner is not None else None
            except Exception:
                pass
        else:
            try:
                lead_data = _prepare_salesforce_data(data, sector)
                lid = crm.create_lead_salesforce(lead_data, owner_id=seller.get("salesforce_user_id"), sector=sector)
                if lid:
                    salesforce_leads_by_contact[contact_id] = {"id": lid, "link": _build_sf_lead_link(lid)}
            except Exception:
                pass

        slack_id = seller.get("slack_user_id")
        summary = _summarize_data({"Setor": sector, **data})
        try:
            if slack_id:
                # Para cidades: Pipedrive, para outros: Salesforce
                sf_link = ""
                pipedrive_link = ""
                if sector_low == "cidades":
                    pipedrive_link = (pipedrive_deals_by_contact.get(contact_id) or {}).get("link") or ""
                else:
                    sf_link = (salesforce_leads_by_contact.get(contact_id) or {}).get("link") or ""
                msg = _build_slack_message(
                    summary=summary,
                    scheduling_success=False,
                    created_event=None,
                    start_dt_scheduled=None,
                    reuniao_urgente=False,
                    cliente_nao_quer_reuniao=False,
                    sf_link=sf_link,
                    pipedrive_link=pipedrive_link
                )
                slack_notify.notify_user(slack_id, msg)
        except Exception:
            pass

        lead_data_by_contact[contact_id] = {"sector": sector, "data": data}
        when_msg = "Quer que eu já agende uma conversa com o vendedor? Se sim, me diga um dia/horário que te fique bom."
        return f"Maravilha! Registrei seus dados e encaminhei ao time certo. {when_msg}"

    q = "Qual o tipo de lugar?"
    sess["last_question"] = q
    sess["pending_questions"] = [q]
    return f"Perfeito! {q}"

    # Última pergunta respondida
    ddd = _extract_ddd(data.get("from"))
    state = data.get("state")
    seller = _route_seller(ddd, state, sess.get("sector"))

    # CRM
    sector = (sess.get("sector") or "").lower()
    pd_owner = seller.get("pipedrive_user_id")
    if sector == "cidades":
        try:
            crm.create_person_and_deal_pipedrive({
                "contact_name": data.get("Contato") or data.get("segmento") or None,
                "email": data.get("email"),
                "phone": data.get("phone"),
                "sector": sess.get("sector"),
                "company": data.get("empresa") or None,
                "source": data.get("source"),
            }, owner_id=pd_owner) if pd_owner is not None else None
        except Exception:
            pass
    else:
        try:
            lead_data = _prepare_salesforce_data(data, sess.get("sector"))
            lid = crm.create_lead_salesforce(lead_data, owner_id=seller.get("salesforce_user_id"), sector=sess.get("sector"))
            if lid:
                salesforce_leads_by_contact[contact_id] = {"id": lid, "link": _build_sf_lead_link(lid)}
        except Exception:
            pass

    # Slack
    slack_id = seller.get("slack_user_id")
    summary = _summarize_data({"Setor": sess.get("sector"), **data})
    try:
        if slack_id:
            # Para cidades: Pipedrive, para outros: Salesforce
            sf_link = ""
            pipedrive_link = ""
            if sector == "cidades":
                pipedrive_link = (pipedrive_deals_by_contact.get(contact_id) or {}).get("link") or ""
            else:
                sf_link = (salesforce_leads_by_contact.get(contact_id) or {}).get("link") or ""
            msg = _build_slack_message(
                summary=summary,
                scheduling_success=False,
                created_event=None,
                start_dt_scheduled=None,
                reuniao_urgente=False,
                cliente_nao_quer_reuniao=False,
                sf_link=sf_link,
                pipedrive_link=pipedrive_link
            )
            slack_notify.notify_user(slack_id, msg)
    except Exception:
        pass

    lead_data_by_contact[contact_id] = {"sector": sess.get("sector"), "data": data}
    when_msg = "Quer que eu já agende uma conversa com o vendedor? Se sim, me diga um dia/horário que te fique bom."
    return f"Maravilha! Registrei seus dados e encaminhei ao time certo. {when_msg}"

def _is_lead_intent(text: str) -> bool:
    low = text.lower()
    # Focar em intenção explícita de orçamento/proposta, sem ativar por palavras genéricas.
    orçamento_terms = ["orçamento", "orcamento", "preço", "preco", "cotação", "cotacao", "proposta", "budget"]
    return any(kw in low for kw in orçamento_terms) and not any(
        kw in low for kw in ["agendar", "reunião", "agenda", "calendar"]
    )


def _is_direct_question(text: str) -> bool:
    low = (text or "").lower()
    direct_terms = [
        "onde fica", "endereço", "endereco", "localização", "localizacao",
        "site", "telefone", "contato", "horário", "horario", "atendimento"
    ]
    q_starts = ["qual", "como", "por que", "porque", "quando", "onde", "quem", "quanto", "que", "o que", "oq"]
    special = ["bhaskara", "baskara", "fórmula", "formula"]
    if any(t in low for t in direct_terms):
        return True
    if "?" in (text or ""):
        return True
    if any(low.startswith(s) for s in q_starts):
        return True
    if any(s in low for s in special):
        return True
    return False

def _is_allowed_topic(text: str) -> bool:
    low = (text or "").lower()
    sectors = (ROUTE_CFG.get("sectors") or {})
    kws = []
    for _, meta in sectors.items():
        for k in meta.get("keywords", []):
            kws.append((k or "").lower())
    allow = set(kws) | {
        "silicon", "orçamento", "orcamento", "preço", "preco", "cotação", "cotacao",
        "agendar", "reunião", "reuniao", "horário", "horario",
        "led", "luminária", "luminaria", "iluminação", "iluminacao",
        "solar", "fotovolta", "fotovoltaica", "gd", "bess", "grow",
        "vendedor", "consultor", "empresa"
    }
    return any(k in low for k in allow)

def _gentle_follow_up(contact_id: str) -> str:
    sess = lead_sessions.get(contact_id) or {}
    data = sess.get("data", {})
    sector = sess.get("sector")
    if not data.get("Contato"):
        return "Para eu te atender melhor, qual é seu nome, por favor?"
    if not (data.get("city") or data.get("localizacao")):
        return "Para avançar com o orçamento, qual a cidade?"
    if sector:
        for q in _questions_for_sector(sector):
            if not data.get(q):
                return q
    return ""


# Removido: respostas prontas. Perguntas diretas serão encaminhadas ao Assistant (OpenAI) ou ao offline.


def _get_sector_calendar(sector: Optional[str]) -> Dict[str, Any]:
    if sector and sector in SCHED_CFG:
        merged = {**DEFAULT_CFG, **SCHED_CFG[sector]}
        return merged
    return DEFAULT_CFG


def _get_tzinfo(tz_name: str):
    return tz.gettz(tz_name) or tz.gettz("UTC")


def _parse_datetime(text: str, tz_name: str) -> Optional[datetime]:
    try:
        low = (text or "").lower()
        tzinfo = _get_tzinfo(tz_name)
        now = datetime.now(tzinfo)
        base = None
        
        # Parse de dias da semana
        weekday_map = {
            'segunda': 0, 'segunda-feira': 0, 'monday': 0,
            'terça': 1, 'terça-feira': 1, 'terca': 1, 'tuesday': 1,
            'quarta': 2, 'quarta-feira': 2, 'wednesday': 2,
            'quinta': 3, 'quinta-feira': 3, 'thursday': 3,
            'sexta': 4, 'sexta-feira': 4, 'friday': 4,
            'sábado': 5, 'sabado': 5, 'saturday': 5,
            'domingo': 6, 'sunday': 6
        }
        
        target_weekday = None
        for day_name, day_num in weekday_map.items():
            if day_name in low:
                target_weekday = day_num
                break
        
        if target_weekday is not None:
            # Calcula dias até o próximo dia da semana
            days_ahead = (target_weekday - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7  # Se for hoje, pega a próxima semana
            base = now + timedelta(days=days_ahead)
        elif any(k in low for k in ["amanhã", "amanha", "tomorrow"]):
            base = now + timedelta(days=1)
        elif any(k in low for k in ["hoje", "today"]):
            base = now
        
        # Parse de horário do dia
        h, m = None, None
        if any(k in low for k in ["de manhã", "de manha", "manhã", "manha", "pela manhã", "pela manha"]):
            h, m = 9, 0
        elif any(k in low for k in ["meio-dia", "meio dia", "12h", "12:00"]):
            h, m = 12, 0
        elif any(k in low for k in ["começo da tarde", "comeco da tarde", "início da tarde", "inicio da tarde"]):
            h, m = 14, 0
        elif any(k in low for k in ["pela tarde", "à tarde", "a tarde", "tarde"]):
            h, m = 15, 0
        elif any(k in low for k in ["fim da tarde", "final da tarde"]):
            h, m = 17, 0
        elif any(k in low for k in ["à noite", "a noite", "noite"]):
            h, m = 19, 0
        
        # Se encontrou dia e horário, retorna
        if base is not None:
            if h is not None:
                dt = base.replace(hour=h, minute=m, second=0, microsecond=0)
                return dt
            else:
                # Se não especificou horário, usa 9h da manhã
                dt = base.replace(hour=9, minute=0, second=0, microsecond=0)
                return dt
        
        # Tenta parsear com dateparser
        dt = dateparser.parse(text, languages=['pt'], dayfirst=True, fuzzy=True)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tzinfo)
        else:
            dt = dt.astimezone(tzinfo)
        return dt
    except Exception:
        return None


def _start_scheduling(contact_id: str, sector: Optional[str] = None) -> str:
    cfg = _get_sector_calendar(sector)
    # Tenta resolver o vendedor para obter calendarId mais preciso
    cal_id = cfg["calendarId"]
    try:
        ddd = _extract_ddd(contact_id)
        seller = _route_seller(ddd, None, sector)
        seller_cal = seller.get("calendarId")
        if seller_cal:
            cal_id = seller_cal
        impersonate = seller.get("email") or None
    except Exception:
        impersonate = None
    scheduling_sessions[contact_id] = {
        "stage": "await_datetime",
        "sector": sector,
        "calendarId": cal_id,
        "timezone": cfg.get("timezone", "America/Sao_Paulo"),
        "duration": int(cfg.get("meetingDurationMinutes", 30)),
        "workStart": cfg.get("workStart", "09:00"),
        "workEnd": cfg.get("workEnd", "18:00"),
        "impersonateEmail": impersonate,
    }
    return (
        "Legal! Aqui é o Eduardo. Você tem um dia e horário em mente para falar com nosso vendedor? "
        "Se preferir, eu sugiro um horário livre pra você."
    )


def _suggest_from_now(session: Dict[str, Any]) -> str:
    tzinfo = _get_tzinfo(session["timezone"])
    now = datetime.now(tzinfo)

    work_start_h, work_start_m = map(int, str(session["workStart"]).split(":"))
    work_end_h, work_end_m = map(int, str(session["workEnd"]).split(":"))
    work_start_time = time(work_start_h, work_start_m)
    work_end_time = time(work_end_h, work_end_m)
    
    # Calcula o horário de início da busca
    # Se ainda há tempo hoje (pelo menos 30 min antes do fim do expediente), tenta hoje
    # Caso contrário, começa amanhã cedo
    if now.time() < work_end_time:
        # Arredonda para o próximo intervalo de 30 minutos
        minutes_to_add = 30 - (now.minute % 30)
        if minutes_to_add == 30:
            minutes_to_add = 0
        start_from = now + timedelta(minutes=minutes_to_add)
        # Se passar do expediente, vai para amanhã
        if start_from.time() >= work_end_time or (start_from + timedelta(minutes=session["duration"])).time() > work_end_time:
            next_day = now.date() + timedelta(days=1)
            # Pula finais de semana
            while next_day.weekday() >= 5:
                next_day += timedelta(days=1)
            start_from = datetime.combine(next_day, work_start_time, tzinfo=tzinfo)
    else:
        # Já passou do expediente, começa amanhã
        next_day = now.date() + timedelta(days=1)
        # Pula finais de semana
        while next_day.weekday() >= 5:
            next_day += timedelta(days=1)
        start_from = datetime.combine(next_day, work_start_time, tzinfo=tzinfo)

    try:
        imp = session.get("impersonateEmail")
        slot = gcal_scheduler.find_next_slot(
            calendar_id=session["calendarId"],
            start_from=start_from,
            duration_minutes=session["duration"],
            work_start=work_start_time,
            work_end=work_end_time,
            impersonate_email=imp,
        )

    except Exception:
        return "Sem problema. Me diz um dia e horário que te fique bom e eu organizo com nosso vendedor."
    if not slot:
        return "Não achei um horário livre nas próximas semanas. Posso tentar outro período pra você?"

    session["proposed"] = slot
    start_local = slot["start"]
    return f"Que tal {start_local.strftime('%d/%m às %H:%M')}?"


def _handle_scheduling(contact_id: str, user_text: str) -> str:
    session = scheduling_sessions.get(contact_id)
    if not session:
        # Sector can be inferred later; start with default
        return _start_scheduling(contact_id)

    stage = session.get("stage")
    tz_name = session.get("timezone", DEFAULT_CFG.get("timezone", "America/Sao_Paulo"))
    cal_id = session["calendarId"]

    if stage == "await_datetime":
        dt = _parse_datetime(user_text, tz_name)
        if not dt:
            # no parseable date/time; suggest next slot
            session["stage"] = "await_confirm"
            return _suggest_from_now(session)

        end_dt = dt + timedelta(minutes=session["duration"])
        try:
            imp = session.get("impersonateEmail")
            if gcal_scheduler.is_free(cal_id, dt, end_dt, impersonate_email=imp):

                session["proposed"] = {"start": dt, "end": end_dt}
                session["stage"] = "await_confirm"
                return f"Esse horário tá livre. Confirmo pra {dt.strftime('%d/%m às %H:%M')}?"
            else:
                session["stage"] = "await_confirm"
                work_start_h, work_start_m = map(int, str(session["workStart"]).split(":"))
                work_end_h, work_end_m = map(int, str(session["workEnd"]).split(":"))
                alt = gcal_scheduler.find_next_slot(
                    calendar_id=cal_id,
                    start_from=dt,
                    duration_minutes=session["duration"],
                    work_start=datetime.now().replace(hour=work_start_h, minute=work_start_m, second=0, microsecond=0).time(),
                    work_end=datetime.now().replace(hour=work_end_h, minute=work_end_m, second=0, microsecond=0).time(),
                    impersonate_email=imp,
                )

                if not alt:
                    return "Esse horário não está livre. Posso sugerir outro dia pra você?"
                session["proposed"] = alt
                return f"Esse horário não está livre. Que tal {alt['start'].strftime('%d/%m às %H:%M')}?"
        except Exception:
            session["proposed"] = {"start": dt, "end": end_dt}
            session["stage"] = "await_confirm"
            return f"Posso tentar confirmar com nosso vendedor para {dt.strftime('%d/%m às %H:%M')}. Pode ser?"

    if stage == "await_confirm":
        text_low = user_text.lower()
        positive = any(k in text_low for k in ["sim", "pode", "confirm", "ok", "feito", "perfeito"])  # confirm intent
        negative = any(k in text_low for k in ["não", "nao", "n", "prefiro outro", "outro", "não posso"])  # refuse intent
        slot = session.get("proposed")
        if positive and slot:
            start_dt = slot["start"]
            end_dt = slot["end"]
            try:
                imp = session.get("impersonateEmail")

                sector = session.get("sector")
                lead_info = lead_data_by_contact.get(contact_id) or {"sector": sector, "data": {}}
                data = lead_info.get("data") or {}
                ddd = _extract_ddd(contact_id)
                seller = _route_seller(ddd, data.get("state"), sector)
                # Para cidades, cria no Pipedrive, não no Salesforce
                sector_low = (sector or "").lower()
                if sector_low == "cidades":
                    # Cidades vai para Pipedrive, não Salesforce
                    if contact_id not in pipedrive_deals_by_contact:
                        try:
                            pd_owner = seller.get("pipedrive_user_id")
                            deal_id = crm.create_person_and_deal_pipedrive({
                                "contact_name": data.get("Contato") or data.get("nome"),
                                "email": data.get("email"),
                                "phone": data.get("phone"),
                                "sector": sector,
                                "company": data.get("empresa"),
                                "source": "WhatsApp"
                            }, owner_id=pd_owner)
                            if deal_id:
                                pipedrive_url = os.getenv('PIPEDRIVE_BASE_URL', 'https://silicon.pipedrive.com').rstrip('/')
                                pipedrive_link = f"{pipedrive_url}/deal/{deal_id}"
                                pipedrive_deals_by_contact[contact_id] = {
                                    "id": deal_id,
                                    "link": pipedrive_link
                                }
                        except Exception:
                            pass
                elif not salesforce_leads_by_contact.get(contact_id):
                    # Outros setores vão para Salesforce
                    try:
                        lead_data = _prepare_salesforce_data(data, sector)
                        lid = crm.create_lead_salesforce(lead_data, owner_id=seller.get("salesforce_user_id"), sector=sector)
                        if lid:
                            salesforce_leads_by_contact[contact_id] = {"id": lid, "link": _build_sf_lead_link(lid)}
                    except Exception:
                        pass
                sf_link = (salesforce_leads_by_contact.get(contact_id) or {}).get("link") or ""
                summary_txt = _summarize_data({"Setor": sector, **(data or {})})
                desc = f"Resumo da conversa:\n{summary_txt}\n{('Lead SF: ' + sf_link) if sf_link else ''}".strip()
                attendees = []
                if data.get("email"):
                    attendees = [data.get("email")]
                # Extrai localização dos dados se disponível
                event_location = None
                if data.get("localizacao"):
                    event_location = data.get("localizacao")
                elif data.get("city"):
                    event_location = data.get("city")
                
                created = gcal_scheduler.create_event(
                    calendar_id=cal_id,
                    summary="Reunião com vendedor",
                    description=desc,
                    start_dt=start_dt,
                    end_dt=end_dt,
                    attendees=attendees,
                    location=event_location,
                    impersonate_email=imp,
                )
                link = created.get("htmlLink", "")
                when_txt = start_dt.strftime('%d/%m/%Y às %H:%M')
                try:
                    slack_id = seller.get("slack_user_id")
                    if slack_id:
                        title = created.get("summary") or "Reunião"
                        sf_link = (salesforce_leads_by_contact.get(contact_id) or {}).get("link") or ""
                        msg = (
                            f"Reunião agendada: {when_txt} – {title}. "
                            f"{('Agenda: ' + link) if link else ''}\n"
                            f"Resumo:\n{summary_txt}\n"
                            f"{('SF: ' + sf_link) if sf_link else ''}"
                        )
                        try:
                            slack_notify.notify_user(slack_id, msg)
                        except Exception:
                            pass
                except Exception:
                    pass
                scheduling_sessions.pop(contact_id, None)
                return f"Perfeito! Agendei pra {when_txt}. Vou te enviar o convite. {('Link: ' + link) if link else ''}"
            except Exception:
                try:
                    ddd = _extract_ddd(contact_id)
                    seller = _route_seller(ddd, None, session.get("sector"))
                    slack_id = seller.get("slack_user_id")
                    if slack_id:
                        when_txt = start_dt.strftime('%d/%m/%Y às %H:%M')
                        msg = f"Lead quer reunião em {when_txt}. Confirmar manualmente."
                        try:
                            slack_notify.notify_user(slack_id, msg)
                        except Exception:
                            pass
                except Exception:
                    pass
                scheduling_sessions.pop(contact_id, None)
                return "Perfeito! Vou confirmar com nosso vendedor e te envio a confirmação em seguida."
            # Notifica o vendedor no Slack (se resolvido pelo roteamento)
            try:
                ddd = _extract_ddd(contact_id)
                # Não temos estado aqui; tentamos com None
                seller = _route_seller(ddd, None, session.get("sector"))
                slack_id = seller.get("slack_user_id")
                if slack_id:
                    when_txt = start_dt.strftime('%d/%m/%Y às %H:%M')
                    title = created.get("summary") or "Reunião"
                    link = created.get("htmlLink") or ""
                    msg = f"Reunião agendada: {when_txt} – {title}. {('Link: ' + link) if link else ''}"
                    try:
                        slack_notify.notify_user(slack_id, msg)
                    except Exception:
                        pass
            except Exception:
                pass
            scheduling_sessions.pop(contact_id, None)
            link = created.get("htmlLink", "")
            when_txt = start_dt.strftime('%d/%m/%Y às %H:%M')
            return f"Perfeito! Agendei pra {when_txt}. Vou te enviar o convite. {('Link: ' + link) if link else ''}"
        elif negative:
            # Propose another
            return _suggest_from_now(session)
        else:
            return "Pra confirmar, é só dizer 'sim'. Se preferir outro horário, me fala."

    # Unknown stage; reset
    scheduling_sessions.pop(contact_id, None)
    return _start_scheduling(contact_id)


class ReplyRequest(BaseModel):
    message: Optional[str] = None
    from_: Optional[str] = Field(default=None, alias="from")
    contact_id: Optional[str] = None  # Alternativa ao from_ para identificar o contato
    thread_id: Optional[str] = None  # Thread_id específico (opcional)
    metadata: Optional[Dict[str, Any]] = None

    model_config = {"populate_by_name": True}

class TranscribeRequest(BaseModel):
    audio_b64: str
    audio_mime: Optional[str] = None


class OutboundVariantRequest(BaseModel):
    base_message: str
    empresa: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    tone: Optional[str] = "aggressive"
    constraints: Optional[Dict[str, Any]] = None


class OutboundReplyClassificationRequest(BaseModel):
    message: str
    phone: Optional[str] = None
    empresa: Optional[str] = None
    outbound_message: Optional[str] = None
    source_context: Optional[Dict[str, Any]] = None


def _fallback_variant(base_message: str, empresa: Optional[str], city: Optional[str], state: Optional[str]) -> str:
    msg = (base_message or "").strip()
    if not msg:
        templates = [
            "Ola, tudo bem?\n\nSou da Silicon e trabalhamos com iluminacao para o aumento de fotossintese de mudas.\n\nVi que voces trabalham com producao de mudas e preciso falar com o responsavel pelo manejo do viveiro.\n\nConsegue me ajudar a falar com o responsavel?",
            "Ola, tudo certo?\n\nSou da Silicon e atuamos com iluminacao para aumentar a fotossintese de mudas.\n\nVi que voces produzem mudas e preciso falar com quem responde pelo manejo do viveiro.\n\nVoce consegue me indicar o responsavel?",
            "Ola, tudo bem?\n\nSou da Silicon e trabalhamos com iluminacao voltada ao aumento de fotossintese de mudas.\n\nPercebi que voces trabalham com producao de mudas e preciso falar com a pessoa responsavel pelo manejo do viveiro.\n\nPode me ajudar a chegar nessa pessoa?"
        ]
        return random.choice(templates)

    variants = [
        msg.replace("Ola, tudo bem?", "Ola, tudo certo?"),
        msg.replace("preciso falar com o responsavel pelo manejo do viveiro.", "queria falar com o responsavel pelo manejo do viveiro."),
        msg.replace("Consegue me ajudar a falar com o responsavel?", "Voce consegue me indicar o responsavel?"),
        msg.replace("Consegue me ajudar a falar com o responsavel?", "Pode me ajudar a chegar nessa pessoa?"),
    ]
    variants = [v for v in variants if v and v.strip()]
    return random.choice(variants) if variants else msg

def ensure_openai_or_offline() -> bool:
    global client, assistant_id, OFFLINE_MODE
    OFFLINE_MODE = False
    if OpenAI is None or not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI não configurado. Defina OPENAI_API_KEY e OPENAI_ASSISTANT_ID.")
    if client is None:
        client = OpenAI(api_key=OPENAI_API_KEY)
    if assistant_id is None:
        if OPENAI_ASSISTANT_ID:
            assistant_id = OPENAI_ASSISTANT_ID
        else:
            try:
                prompt_path = Path(__file__).resolve().parent / "assistant.system.txt"
                if prompt_path.exists():
                    with open(prompt_path, "r", encoding="utf-8") as f:
                        system_prompt = f.read()
                else:
                    system_prompt = (
                        "Você é um assistente SDR de vendas. Fale de forma humana, "
                        "clara e acolhedora. Se faltar dados, peça com perguntas curtas."
                    )
            except Exception:
                system_prompt = (
                    "Você é um assistente SDR de vendas. Fale de forma humana, "
                    "clara e acolhedora. Se faltar dados, peça com perguntas curtas."
                )
            tools = []
            try:
                func_path = Path(__file__).resolve().parent / "assistant.functions.json"
                if func_path.exists():
                    with open(func_path, "r", encoding="utf-8") as ff:
                        func_cfg = json.load(ff)
                    for fn in func_cfg.get("functions", []):
                        tools.append({
                            "type": "function",
                            "function": {
                                "name": fn.get("name"),
                                "description": fn.get("description"),
                                "parameters": fn.get("parameters"),
                            },
                        })
            except Exception:
                tools = []
            a = client.beta.assistants.create(
                name="IA WhatsApp SDR",
                instructions=system_prompt,
                model=MODEL,
                tools=tools or None,
            )
            assistant_id = a.id
        # Atualiza instruções mesmo quando há ASSISTANT_ID definido
        try:
            prompt_path = Path(__file__).resolve().parent / "assistant.system.txt"
            if prompt_path.exists():
                with open(prompt_path, "r", encoding="utf-8") as f:
                    system_prompt = f.read()
            else:
                system_prompt = ""
            tools = []
            try:
                func_path = Path(__file__).resolve().parent / "assistant.functions.json"
                if func_path.exists():
                    with open(func_path, "r", encoding="utf-8") as ff:
                        func_cfg = json.load(ff)
                    for fn in func_cfg.get("functions", []):
                        tools.append({
                            "type": "function",
                            "function": {
                                "name": fn.get("name"),
                                "description": fn.get("description"),
                                "parameters": fn.get("parameters"),
                            },
                        })
            except Exception:
                tools = []
            client.beta.assistants.update(assistant_id, instructions=system_prompt, tools=tools or None, model=MODEL)
        except Exception:
            pass
    return True


def _offline_reply(contact_id: str, user_text: str) -> str:
    """Resposta local com tom humano, sem mencionar IA."""
    text = (user_text or "").strip()
    low = text.lower()

    greetings = [
        "Oi, tudo bem? Me diz, por favor, como posso ajudar.",
        "Oi! Tudo certo? Me conta, por favor, o que você precisa.",
        "Olá! Tudo bem? Posso te ajudar, me diz por favor.",
    ]
    ask_budget = [
        "Claro! Me diz, por favor, o que você precisa e a cidade.",
        "Perfeito. Para te ajudar bem, qual a necessidade e de qual cidade?",
        "Combinado! O que você precisa e de qual cidade, por favor?",
    ]
    ask_schedule = [
        "Posso te ajudar a agendar. Qual dia e horário você prefere?",
        "Certo, podemos agendar. Me diz um dia e horário que te fique bom.",
        "Vamos agendar sim. Qual janela de horário funciona pra você?",
    ]
    ask_default = [
        "Entendi. Me diz, por favor, o que você precisa e a cidade.",
        "Tá bom! Para te ajudar, o que você precisa e de qual cidade?",
        "Certo! Me conta, por favor, a necessidade e a cidade.",
    ]

    if not text or any(k in low for k in ["oi", "ola", "olá", "bom dia", "boa tarde", "boa noite"]):
        return random.choice(greetings)
    if any(k in low for k in ["orçamento", "orcamento", "preço", "preco", "cotação", "cotacao"]):
        return random.choice(ask_budget)
    if any(k in low for k in ["agendar", "reunião", "reuniao", "horário", "horario", "agenda"]):
        return random.choice(ask_schedule)
    return random.choice(ask_default)


def _sanitize_reply(text: str) -> str:
    """
    Remove mensagens de contexto interno e sanitiza a resposta antes de enviar ao WhatsApp.
    """
    try:
        if not text:
            return ""
        
        # Remove mensagens [CONTEXTO INTERNO: ...] da resposta
        import re
        # Remove padrões como [CONTEXTO INTERNO: ...] ou [CONTEXTO INTERNO ...]
        text = re.sub(r'\[CONTEXTO INTERNO[^\]]*\]', '', text, flags=re.IGNORECASE)
        
        # Remove linhas vazias extras
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        text = '\n'.join(lines)
        
        return text.strip()
    except Exception:
        return text

def _is_product_inquiry(text: str) -> bool:
    low = (text or "").lower()
    tokens = [
        "luminária", "luminaria", "luminárias", "luminarias",
        "produto", "produtos", "modelo", "modelos",
        "linha", "linhas", "catálogo", "catalogo",
        "alba", "audace", "sunna", "smart", "grow",
    ]
    return any(t in low for t in tokens)

def _product_info_snippet(text: str) -> str:
    try:
        import product_kb
        kb = product_kb.query_product_knowledge(text, top_k=1)
        items = kb.get("excel_results") or []
        if items:
            it = items[0]
            name = it.get("name") or ""
            desc = it.get("description") or it.get("features") or ""
            link = it.get("link") or ""
            core = f"{name}: {desc}".strip()
            info = core if core else ""
            if link:
                info = f"{info}. Link: {link}".strip()
            return info
    except Exception:
        pass
    return ""

def _strip_html(s: str) -> str:
    try:
        return re.sub(r"<[^>]+>", "", s or "").strip()
    except Exception:
        return s or ""

def _default_marketing_sources() -> list:
    return [
        "https://www.silicon.ind.br/conteudos",
        "https://www.instagram.com/silicon.energy/",
        "https://www.silicon.ind.br/quem-somos",
        "https://www.linkedin.com/company/silicon-energy-led/posts/?feedView=all",
    ]

def _topic_keywords(topic: str) -> list:
    t = (topic or "").lower()
    if any(k in t for k in ["cênic", "cenic", "cenografia", "palco", "evento", "show"]):
        return ["cênic", "cenic", "cenografia", "palco", "evento", "show", "dmx"]
    if any(k in t for k in ["esport", "arena", "quadra", "estádio", "estadio", "castelão", "castelao"]):
        return ["esport", "arena", "quadra", "estádio", "estadio", "campo", "iluminação esportiva", "iluminacao esportiva"]
    if any(k in t for k in ["industrial", "galp", "fábr", "fabr"]):
        return ["industrial", "galp", "fábr", "fabr", "linha", "produção", "producao"]
    if any(k in t for k in ["grow", "cultivo", "estufa"]):
        return ["grow", "cultivo", "estufa", "horta", "hidrop"]
    return [t]

def _matches_any(text: str, kws: list) -> bool:
    tl = (text or "").lower()
    for k in kws:
        if k and k in tl:
            return True
    return False

def _query_marketing_content(topic: str, sources: list, max_items: int) -> list:
    items = []
    kws = _topic_keywords(topic)
    for url in sources or _default_marketing_sources():
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            txt = r.text
            hs = re.findall(r"<h[12][^>]*>(.*?)</h[12]>", txt, flags=re.I | re.S)
            ps = re.findall(r"<p[^>]*>(.*?)</p>", txt, flags=re.I | re.S)
            pairs = []
            for h in hs[:20]:
                th = _strip_html(h)
                if th:
                    pairs.append({"title": th, "snippet": th})
            for p in ps[:60]:
                tp = _strip_html(p)
                if tp:
                    pairs.append({"title": "", "snippet": tp})
            relevant = []
            for it in pairs:
                t = (it.get("title") or it.get("snippet") or "")
                if not t:
                    continue
                if _matches_any(t, kws):
                    relevant.append({"title": it.get("title") or t[:60], "snippet": t, "url": url})
                    if len(relevant) >= max_items:
                        break
            selected = relevant
            if not selected:
                for it in pairs:
                    t = (it.get("title") or it.get("snippet") or "")
                    if not t:
                        continue
                    selected.append({"title": it.get("title") or t[:60], "snippet": t, "url": url})
                    if len(selected) >= max_items:
                        break
            for s in selected:
                items.append(s)
                if len(items) >= max_items:
                    break
        except Exception:
            items.append({"title": "Conteúdo", "snippet": "", "url": url})
        if len(items) >= max_items:
            break
    return items

def _execute_tool_call(name: str, arguments: Dict[str, Any], thread_id: Optional[str] = None) -> Any:
    """Executa ferramentas solicitadas pelo Assistant usando implementações locais."""
    try:
        # Obtém contact_id do thread_id para isolamento
        contact_id = contact_by_thread.get(thread_id) if thread_id else None
        
        if name == "route_seller":
            sector = arguments.get("sector")
            phone = arguments.get("phone")
            ddd = _extract_ddd(phone)
            seller = _route_seller(ddd, None, sector)
            if contact_id:
                seller_by_contact[contact_id] = seller
            return seller
        if name == "query_product_knowledge":
            # Busca conhecimento de produtos no Excel e site
            try:
                import product_kb  # lazy import para não quebrar se dependências do Excel faltarem
            except Exception as e:
                return {"error": f"Falha ao importar product_kb: {e}"}
            query = arguments.get("query") or ""
            top_k = int(arguments.get("top_k") or 3)
            return product_kb.query_product_knowledge(query, top_k)
        if name == "query_marketing_content":
            topic = arguments.get("topic") or ""
            max_items = int(arguments.get("max_items") or 3)
            sources = arguments.get("sources") or _default_marketing_sources()
            return {"items": _query_marketing_content(topic, sources, max_items)}
        if name == "is_free":
            cal_id = arguments.get("calendar_id") or "primary"
            start_dt = dateparser.parse(arguments.get("start_dt"))
            end_dt = dateparser.parse(arguments.get("end_dt"))
            imp = (seller_by_contact.get(contact_id) or {}).get("email") if contact_id else None
            try:
                result = gcal_scheduler.is_free(cal_id, start_dt, end_dt, impersonate_email=imp)
                return {"free": bool(result)}
            except Exception as e:
                print(f"[CALENDAR] Erro ao verificar disponibilidade: {e}")
                return {"free": True, "error": str(e)}  # Assume livre em caso de erro
        if name == "find_next_slots":
            cal_id = arguments.get("calendar_id") or "primary"
            start_from = arguments.get("start_from") or ""
            days_ahead = int(arguments.get("days_ahead") or 14)
            duration_min = int(arguments.get("duration_min") or 30)
            tz_name = arguments.get("timezone") or "America/Sao_Paulo"
            tzinfo = _get_tzinfo(tz_name)
            
            # Parse da data/hora inicial
            now = datetime.now(tzinfo)
            if start_from:
                # Tenta parsear a data/hora
                start_dt = dateparser.parse(start_from, languages=['pt'], settings={'TIMEZONE': 'America/Sao_Paulo', 'PREFER_DATES_FROM': 'future'})
                if not start_dt:
                    # Se não conseguir parsear, tenta interpretar expressões comuns
                    low = start_from.lower()
                    if "segunda" in low or "monday" in low:
                        # Próxima segunda
                        days_until_monday = (7 - now.weekday()) % 7
                        if days_until_monday == 0:
                            days_until_monday = 7
                        start_dt = now + timedelta(days=days_until_monday)
                        start_dt = start_dt.replace(hour=9, minute=0, second=0, microsecond=0)
                    elif "terça" in low or "tuesday" in low:
                        days_until_tuesday = (1 - now.weekday()) % 7
                        if days_until_tuesday == 0:
                            days_until_tuesday = 7
                        start_dt = now + timedelta(days=days_until_tuesday)
                        start_dt = start_dt.replace(hour=9, minute=0, second=0, microsecond=0)
                    elif "amanhã" in low or "amanha" in low or "tomorrow" in low:
                        start_dt = now + timedelta(days=1)
                        start_dt = start_dt.replace(hour=9, minute=0, second=0, microsecond=0)
                    elif "hoje" in low or "today" in low:
                        # Se pediu hoje, tenta encontrar horário para hoje ainda
                        # Arredonda para o próximo intervalo de 30 minutos
                        minutes_to_add = 30 - (now.minute % 30)
                        if minutes_to_add == 30:
                            minutes_to_add = 0
                        start_dt = now + timedelta(minutes=minutes_to_add)
                    else:
                        # Se não especificou, tenta hoje primeiro (se houver tempo)
                        minutes_to_add = 30 - (now.minute % 30)
                        if minutes_to_add == 30:
                            minutes_to_add = 0
                        start_dt = now + timedelta(minutes=minutes_to_add)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=tzinfo)
            else:
                # Se não especificou data, tenta hoje primeiro (se houver tempo)
                minutes_to_add = 30 - (now.minute % 30)
                if minutes_to_add == 30:
                    minutes_to_add = 0
                start_dt = now + timedelta(minutes=minutes_to_add)
            
            # Ajusta para horário comercial se necessário (8h-17:30)
            if start_dt.time() < time(8, 0):
                start_dt = start_dt.replace(hour=8, minute=0, second=0, microsecond=0)
            elif start_dt.time() >= time(17, 30) or (start_dt + timedelta(minutes=duration_min)).time() > time(17, 30):
                # Se passou do expediente ou não há tempo suficiente hoje, vai para amanhã
                next_day = start_dt.date() + timedelta(days=1)
                # Pula finais de semana
                while next_day.weekday() >= 5:
                    next_day += timedelta(days=1)
                start_dt = datetime.combine(next_day, time(8, 0), tzinfo=tzinfo)
            # Pula horário de almoço (12h-13h)
            elif time(12, 0) <= start_dt.time() < time(13, 0):
                start_dt = start_dt.replace(hour=13, minute=0, second=0, microsecond=0)
            
            work_start = time(8, 0)  # Início: 8h
            work_end = time(17, 30)  # Fim: 17:30
            imp = (seller_by_contact.get(contact_id) or {}).get("email") if contact_id else None
            print(f"[CALENDAR] find_next_slots: cal_id={cal_id}, contact_id={contact_id}, impersonate={imp}, start={start_dt}")
            
            slots = []
            # Tenta encontrar até 3 sugestões dentro do período
            current = start_dt
            end_limit = start_dt + timedelta(days=days_ahead)
            while len(slots) < 3 and current < end_limit:
                try:
                    slot = gcal_scheduler.find_next_slot(
                        calendar_id=cal_id,
                        start_from=current,
                        duration_minutes=duration_min,
                        work_start=work_start,
                        work_end=work_end,
                        max_days_ahead=days_ahead,
                        impersonate_email=imp,
                    )
                    if not slot:
                        break
                    slots.append({
                        "start_dt": slot["start"].isoformat(),
                        "end_dt": slot["end"].isoformat(),
                    })
                    current = slot["end"] + timedelta(minutes=30)
                except Exception as e:
                    print(f"[CALENDAR] Erro ao buscar slot: {e}")
                    # Se der erro, tenta continuar
                    current = current + timedelta(hours=1)
                    if current > end_limit:
                        break
            
            if not slots:
                # Se não encontrou slots, retorna sugestões padrão
                print(f"[CALENDAR] Nenhum slot encontrado, retornando sugestões padrão")
                now = datetime.now(_get_tzinfo("America/Sao_Paulo"))
                # Sugere próximo dia útil às 8h, 10h e 14h (evitando almoço 12h-13h)
                next_day = now + timedelta(days=1)
                if next_day.weekday() >= 5:  # Sábado ou Domingo
                    next_day = next_day + timedelta(days=(7 - next_day.weekday()))
                slots = [
                    {"start_dt": next_day.replace(hour=8, minute=0, second=0, microsecond=0).isoformat(), "end_dt": next_day.replace(hour=8, minute=30, second=0, microsecond=0).isoformat()},
                    {"start_dt": next_day.replace(hour=10, minute=0, second=0, microsecond=0).isoformat(), "end_dt": next_day.replace(hour=10, minute=30, second=0, microsecond=0).isoformat()},
                    {"start_dt": next_day.replace(hour=14, minute=0, second=0, microsecond=0).isoformat(), "end_dt": next_day.replace(hour=14, minute=30, second=0, microsecond=0).isoformat()},
                ]
            
            return {"slots": slots}
        if name == "create_event":
            cal_id = arguments.get("calendar_id") or "primary"
            start_dt_str = arguments.get("start_dt")
            end_dt_str = arguments.get("end_dt")
            summary = arguments.get("summary")
            description = arguments.get("description")
            attendees = arguments.get("attendees") or []
            location = arguments.get("location") or None
            
            # Parse das datas com timezone
            tz_name = "America/Sao_Paulo"
            tzinfo = _get_tzinfo(tz_name)
            
            start_dt = dateparser.parse(start_dt_str, languages=['pt'], settings={'TIMEZONE': 'America/Sao_Paulo'})
            end_dt = dateparser.parse(end_dt_str, languages=['pt'], settings={'TIMEZONE': 'America/Sao_Paulo'})
            
            if not start_dt or not end_dt:
                return {"error": "Não foi possível parsear as datas", "event": None, "success": False}
            
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=tzinfo)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=tzinfo)
            
            imp = (seller_by_contact.get(contact_id) or {}).get("email") if contact_id else None
            print(f"[CALENDAR] create_event: cal_id={cal_id}, contact_id={contact_id}, impersonate={imp}, start={start_dt}, attendees={attendees}")
            
            try:
                created = gcal_scheduler.create_event(
                    calendar_id=cal_id,
                    summary=summary or "Reunião Silicon",
                    description=description or "",
                    start_dt=start_dt,
                    end_dt=end_dt,
                    attendees=attendees,
                    location=location,
                    impersonate_email=imp,
                )
                print(f"[CALENDAR] Evento criado com sucesso: {created.get('id')}")
                return {"event": created, "success": True}
            except Exception as e:
                print(f"[CALENDAR] Erro ao criar evento: {e}")
                return {"error": str(e), "event": None, "success": False}
        if name == "notify_seller_slack":
            slack_user_id = arguments.get("slack_user_id")
            text = arguments.get("text")
            print(f"[SLACK] notify_seller_slack: contact_id={contact_id}, slack_user_id={slack_user_id}, text_length={len(text) if text else 0}")
            try:
                ok = False
                if slack_user_id:
                    ok = bool(slack_notify.notify_user(slack_user_id, text))
                    if ok:
                        print(f"[SLACK] Mensagem enviada com sucesso para slack_user_id={slack_user_id}")
                if not ok:
                    seller = seller_by_contact.get(contact_id) or {} if contact_id else {}
                    email = seller.get("email")
                    if email:
                        print(f"[SLACK] Tentando enviar para email={email}")
                        ok = bool(slack_notify.notify_user(email, text))
                        if ok:
                            print(f"[SLACK] Mensagem enviada com sucesso para email={email}")
                if not ok:
                    print(f"[SLACK] Erro: Não foi possível enviar mensagem")
                return {"ok": bool(ok)}
            except Exception as e:
                print(f"[SLACK] Erro ao enviar mensagem: {e}")
                return {"ok": False, "error": str(e)}
        if name == "summarize_lead":
            sector = arguments.get("sector")
            data = arguments.get("data") or {}
            return {"summary": _summarize_data({"Setor": sector, **data})}
        if name == "classify_sector":
            text = arguments.get("text") or ""
            s = _detect_sector(text) or _sector_from_text(text) or ""
            return {"sector": s}
        if name == "extract_sector_data":
            sector = (arguments.get("sector") or "").lower()
            text = arguments.get("text") or ""
            base = _extract_fields(text)
            city_state = _parse_city_state(text)
            if city_state.get("city") and not base.get("city"):
                base["city"] = city_state.get("city")
            if city_state.get("state") and not base.get("state"):
                base["state"] = city_state.get("state")
            cfg_path = Path(__file__).resolve().parent / "assistant.config.json"
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}
            s_cfg = (cfg.get("sectors") or {}).get(sector) or {}
            fields = [f.get("key") for f in (s_cfg.get("extraction_fields") or []) if f.get("key")]
            pending = [k for k in fields if not base.get(k)]
            return {"data": base, "pending": pending}
        if name == "create_lead_salesforce":
            owner_id = arguments.get("owner_id") or None
            data = arguments.get("data") or {}
            sector = data.get("sector") or arguments.get("sector")
            print(f"[SALESFORCE] create_lead_salesforce: contact_id={contact_id}, owner_id={owner_id}, sector={sector}")
            # Se os dados já vieram preparados, usa direto, senão prepara
            if not any(k in data for k in ["tipo_lugar", "tipo_local", "cnpj", "cultivo"]):
                data = _prepare_salesforce_data(data, sector)
            lid = crm.create_lead_salesforce(data, owner_id=owner_id, sector=sector)
            if lid:
                print(f"[SALESFORCE] Lead criado com sucesso: id={lid}")
            else:
                print(f"[SALESFORCE] Erro: Lead não foi criado (retornou None)")
            return {"id": lid or ""}
        if name == "create_person_and_deal_pipedrive":
            owner_id = int(arguments.get("owner_id") or 0) or None
            data = arguments.get("data") or {}
            pid = crm.create_person_and_deal_pipedrive(data, owner_id) if owner_id is not None else None
            return {"id": pid or ""}
    except Exception as e:
        return {"error": str(e)}


def _handle_run_with_tools(thread_id: str, run_id: str, timeout_seconds: int = 60) -> None:
    """Loop de execução que trata requires_action com tool calls e aguarda conclusão."""
    start = time_module.time()
    while True:
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_id)
        if run.status == "requires_action":
            try:
                tool_calls = run.required_action.submit_tool_outputs.tool_calls
                outputs = []
                for tc in tool_calls:
                    name = tc.function.name
                    args = json.loads(tc.function.arguments or "{}")
                    result = _execute_tool_call(name, args, thread_id)
                    outputs.append({"tool_call_id": tc.id, "output": json.dumps(result, ensure_ascii=False)})
                client.beta.threads.runs.submit_tool_outputs(
                    thread_id=thread_id,
                    run_id=run_id,
                    tool_outputs=outputs,
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Falha ao executar ferramenta: {e}")
        elif run.status in ("completed", "failed", "cancelled", "expired"):
            if run.status != "completed":
                raise HTTPException(status_code=500, detail=f"Run terminou com status: {run.status}")
            return
        elif time_module.time() - start > timeout_seconds:
            raise HTTPException(status_code=504, detail="Timeout aguardando resposta do Assistant")
        else:
            time_module.sleep(1)


@app.post("/reply")
def reply(req: ReplyRequest):
    try:
        print(f"[REPLY] ========== INÍCIO ==========")
        print(f"[REPLY] Request recebido: {req}")
        user_text = req.message or ""
        # Usa contact_id se fornecido, senão usa from_, senão "anon"
        contact_id = req.contact_id or req.from_ or "anon"
        _preload_contact_context(contact_id)

        # Log para debug
        print(f"[REPLY] contact_id={contact_id}, message={user_text[:50]}...")
        print(f"[REPLY] conversation_history keys: {list(conversation_history.keys())}")
        print(f"[REPLY] run_lock_by_contact keys: {list(run_lock_by_contact.keys())}")
        
        # Lock para evitar processamento simultâneo do MESMO contato
        locked = False
        start_wait = time_module.time()
        while not locked:
            with _state_lock:
                if not run_lock_by_contact.get(contact_id):
                    run_lock_by_contact[contact_id] = True
                    locked = True
            
            if not locked:
                if time_module.time() - start_wait > 8:
                    return {"reply": _sanitize_reply("Só um instante, estou finalizando sua resposta anterior.")}
                time_module.sleep(0.25)

        print(f"[REPLY] Lock adquirido para {contact_id}")
    except Exception as init_error:
        print(f"[REPLY] ❌ ERRO na inicialização: {init_error}")
        import traceback
        traceback.print_exc()
        return {"reply": "Desculpe, ocorreu um erro ao processar sua mensagem. Tente novamente."}
    
    try:
        
        # Recarrega system prompt para garantir que está atualizado
        current_prompt = _load_system_prompt()
        
        # Inicializa histórico se não existir
        if contact_id not in conversation_history:
            conversation_history[contact_id] = [
                {"role": "system", "content": current_prompt}
            ]
        else:
            # Atualiza system prompt se já existe histórico
            if conversation_history[contact_id] and conversation_history[contact_id][0].get("role") == "system":
                conversation_history[contact_id][0]["content"] = current_prompt
        
        # Adiciona mensagem do usuário ao histórico
        conversation_history[contact_id].append({
            "role": "user",
            "content": user_text
        })
        
        # Chama DeepSeek
        print(f"[DEEPSEEK] Chamando API para contact_id={contact_id}")
        # Log do system prompt para debug
        if conversation_history[contact_id] and conversation_history[contact_id][0].get("role") == "system":
            prompt_preview = conversation_history[contact_id][0]["content"][:200]
            print(f"[DEEPSEEK] System prompt (primeiros 200 chars): {prompt_preview}...")
        try:
            assistant_reply = ""
            for attempt in range(DEEPSEEK_MAX_ATTEMPTS):
                assistant_reply = deepseek_client.chat_completion(
                    conversation_history[contact_id],
                    temperature=0.2,
                    max_tokens=512,
                    top_p=0.9,
                    stream=False  # Desabilitado temporariamente para debug
                )
                print(f"[DEEPSEEK] ✅ Resposta recebida: {len(assistant_reply) if assistant_reply else 0} caracteres (tentativa {attempt + 1})")
                if not assistant_reply or len(assistant_reply.strip()) == 0:
                    print(f"[DEEPSEEK] ⚠️ Resposta vazia, usando prompt alternativo")
                    assistant_reply = "Olá! Como posso ajudar você hoje?"
                if assistant_reply.strip() == FALLBACK_REPLY and attempt + 1 < DEEPSEEK_MAX_ATTEMPTS:
                    print(f"[DEEPSEEK] ⚠️ Resposta igual ao fallback técnico, reexecutando ({attempt + 1}/{DEEPSEEK_MAX_ATTEMPTS})")
                    continue
                break
            
            import re
            horario_patterns = [
                r'amanhã\s+às\s+(\d{1,2})[h:]?',
                r'às\s+(\d{1,2})[h:]?',
                r'(\d{1,2})[h:]?\s+funciona',
                r'que tal\s+.*?(\d{1,2})[h:]?',
            ]

            horario_encontrado = None
            for pattern in horario_patterns:
                match = re.search(pattern, assistant_reply, re.IGNORECASE)
                if match:
                    hora = int(match.group(1))
                    if "amanhã" in assistant_reply.lower() or "amanha" in assistant_reply.lower():
                        from datetime import datetime, timedelta, time
                        tzinfo = _get_tzinfo("America/Sao_Paulo")
                        now = datetime.now(tzinfo)
                        tomorrow = now + timedelta(days=1)
                        horario_encontrado = datetime.combine(tomorrow.date(), time(hora, 0), tzinfo)
                    else:
                        from datetime import datetime, timedelta, time
                        tzinfo = _get_tzinfo("America/Sao_Paulo")
                        now = datetime.now(tzinfo)
                        tomorrow = now + timedelta(days=1)
                        horario_encontrado = datetime.combine(tomorrow.date(), time(hora, 0), tzinfo)
                    break

            if horario_encontrado:
                seller = seller_by_contact.get(contact_id) or {}
                if seller and seller.get("email"):
                    cal_id = seller.get("calendarId") or seller.get("calendar_id") or "primary"
                    impersonate_email = seller.get("email")

                    from datetime import timedelta
                    end_dt = horario_encontrado + timedelta(minutes=30)
                    is_available = gcal_scheduler.is_free(cal_id, horario_encontrado, end_dt, impersonate_email=impersonate_email)

                    if not is_available:
                        print(f"[FLUXO] ⚠️ Horário sugerido ({horario_encontrado.strftime('%H:%M')}) NÃO está disponível - buscando próximo slot...")
                        from datetime import time
                        slot = gcal_scheduler.find_next_slot(
                            calendar_id=cal_id,
                            start_from=horario_encontrado,
                            duration_minutes=30,
                            work_start=time(8, 0),
                            work_end=time(17, 30),
                            lunch_start=time(12, 0),
                            lunch_end=time(13, 0),
                            impersonate_email=impersonate_email
                        )

                        if slot:
                            novo_horario = slot["start"]
                            novo_horario_txt = novo_horario.strftime('%d/%m/%Y às %H:%M')
                            horario_original_txt = horario_encontrado.strftime('%H:%M')

                            assistant_reply = re.sub(
                                r'amanhã\s+às\s+\d{1,2}[h:]?',
                                f'{novo_horario_txt}',
                                assistant_reply,
                                flags=re.IGNORECASE
                            )
                            assistant_reply = re.sub(
                                r'às\s+\d{1,2}[h:]?',
                                f'às {novo_horario.strftime("%H:%M")}',
                                assistant_reply,
                                flags=re.IGNORECASE
                            )

                            print(f"[FLUXO] ✅ Horário substituído: {horario_original_txt} -> {novo_horario.strftime('%H:%M')}")
                        else:
                            print(f"[FLUXO] ⚠️ Nenhum slot disponível encontrado após horário sugerido")
        except Exception as deepseek_error:
            print(f"[DEEPSEEK] ❌ ERRO ao chamar DeepSeek: {deepseek_error}")
            import traceback
            error_trace = traceback.format_exc()
            print(f"[DEEPSEEK] Traceback completo:")
            print(error_trace)
            # Em caso de erro, usa resposta de fallback ao invés de quebrar
            print(f"[DEEPSEEK] ⚠️ Usando resposta de fallback devido ao erro")
            assistant_reply = "Olá! Desculpe, estou com dificuldades técnicas no momento. Pode repetir sua mensagem?"
        
        # Adiciona resposta ao histórico
        conversation_history[contact_id].append({
            "role": "assistant",
            "content": assistant_reply
        })
        
        # Extrai dados automaticamente
        print(f"[EXTRACT] Extraindo dados para contact_id={contact_id}")
        extracted_data = data_extractor.extract_lead_data(
            conversation_history[contact_id],
            contact_id
        )
        
        # Atualiza dados do lead
        if extracted_data:
            current_data = lead_data_by_contact.get(contact_id, {})
            if isinstance(current_data, dict) and isinstance(current_data.get("data"), dict):
                current_data["data"].update(extracted_data)
                lead_data_by_contact[contact_id] = current_data
            else:
                current_data.update(extracted_data)
                lead_data_by_contact[contact_id] = current_data
            print(f"[EXTRACT] Dados atualizados: {list(current_data.keys())}")
            
            # Detecta setor e roteia vendedor (sempre que houver setor e não houver vendedor roteado)
            data_for_routing = current_data.get("data") if isinstance(current_data, dict) and isinstance(current_data.get("data"), dict) else current_data
            sector = (current_data.get("sector") if isinstance(current_data, dict) else None) or data_for_routing.get("setor") or extracted_data.get("setor")
            if sector and contact_id not in seller_by_contact:
                # Roteia vendedor
                ddd = _extract_ddd(contact_id)
                estado = data_for_routing.get("estado") or extracted_data.get("estado")
                seller = _route_seller(ddd, estado, sector)
                seller_by_contact[contact_id] = seller
                seller_name = seller.get('name', 'N/A')
                print(f"[ROUTING] Setor detectado: {sector}, Estado: {estado}, DDD: {ddd}, Vendedor: {seller_name}")
                
                # Injeta informação do vendedor no contexto da conversa
                if seller_name and seller_name != "N/A" and seller_name != "Vendedor Default" and conversation_history.get(contact_id):
                    # Adiciona mensagem do sistema com o nome do vendedor logo após o system prompt inicial
                    routing_msg = {
                        "role": "system",
                        "content": f"VENDEDOR ROTEADO: {seller_name}. Use o nome '{seller_name}' ao mencionar o vendedor na conversa. Exemplo: 'Vou te conectar com o {seller_name}' ou 'Posso agendar uma reunião com o {seller_name}'."
                    }
                    # Insere após o system prompt (índice 0) e antes das mensagens do usuário
                    if len(conversation_history[contact_id]) > 0:
                        conversation_history[contact_id].insert(1, routing_msg)
                    else:
                        conversation_history[contact_id].append(routing_msg)
            
            # NOTA: Criação de lead no Salesforce movida para o fluxo principal abaixo
        
        # Analisa ações - analisa a MENSAGEM DO USUÁRIO para detectar intenção de agendamento
        print(f"[ACTION] ===== INÍCIO ANÁLISE DE AÇÕES =====")
        print(f"[ACTION] Contact ID: {contact_id}")
        print(f"[ACTION] Mensagem do usuário: {user_text[:100]}...")
        actions = action_analyzer.analyze_actions(
            user_text,  # Analisa a mensagem do USUÁRIO, não a resposta do assistente
            conversation_history[contact_id],
            contact_id
        )
        print(f"[ACTION] Ações detectadas: {json.dumps(actions, ensure_ascii=False, indent=2)}")
        print(f"[ACTION] needs_scheduling = {actions.get('needs_scheduling')}")
        print(f"[ACTION] scheduling_info = {actions.get('scheduling_info', {})}")
        
        # ===== FLUXO PRINCIPAL: AGENDAMENTO, SALESFORCE E SLACK =====
        print(f"\n{'='*80}")
        print(f"[FLUXO] ===== INÍCIO DO FLUXO PRINCIPAL =====")
        print(f"[FLUXO] Contact ID: {contact_id}")
        print(f"{'='*80}\n")
        
        # 1. Verifica se todos os dados estão coletados
        lead_data_raw = lead_data_by_contact.get(contact_id, {})
        
        # Normaliza formato dos dados (pode vir como {"sector": ..., "data": ...} ou direto)
        if isinstance(lead_data_raw, dict) and "data" in lead_data_raw:
            lead_data = lead_data_raw.get("data", {})
            sector = lead_data_raw.get("sector") or lead_data.get("setor")
        else:
            lead_data = lead_data_raw
            sector = lead_data.get("setor")
        
        nome = lead_data.get("nome") or lead_data.get("Contato")
        email = lead_data.get("email")
        cidade = lead_data.get("cidade") or lead_data.get("city")
        telefone = lead_data.get("telefone") or lead_data.get("phone")
        empresa = lead_data.get("empresa") or lead_data.get("company")
        
        # Telefone: se não tiver coletado, usa o contact_id (já está no WhatsApp)
        if not telefone:
            contact_id_clean = contact_id.replace("55", "").replace("+", "").replace("-", "").replace(" ", "")
            if contact_id_clean and len(contact_id_clean) >= 10:
                telefone = contact_id_clean
                lead_data["phone"] = contact_id_clean
                lead_data["telefone"] = contact_id_clean
                print(f"[FLUXO] Telefone extraído do contact_id: {contact_id_clean}")
        
        # Empresa: se não tiver coletado, tenta usar do contexto se disponível
        if not empresa and "empresa" in lead_data_by_contact.get(contact_id, {}).get("data", {}):
             empresa = lead_data_by_contact[contact_id]["data"]["empresa"]
             print(f"[FLUXO] Empresa extraída do contexto: {empresa}")

        # Verifica dados básicos (telefone não é obrigatório, pois já está no WhatsApp)
        dados_basicos_completos = sector and nome and email and cidade and empresa
        
        # Verifica se todas as perguntas do setor foram respondidas
        perguntas_setor_completas = True
        perguntas_faltando = []
        if sector:
            questions_for_sector = _questions_for_sector(sector)
            for q in questions_for_sector:
                # Mapeia pergunta para campo de dados
                q_low = q.lower()
                campo_encontrado = False
                if "cidade" in q_low or "localização" in q_low or "localizacao" in q_low:
                    campo_encontrado = bool(cidade)
                elif "segmento" in q_low:
                    campo_encontrado = bool(lead_data.get("segmento") or lead_data.get("segmento_empresa"))
                elif "público" in q_low or "publico" in q_low or "privado" in q_low:
                    campo_encontrado = bool(lead_data.get("tipo_cliente"))
                elif "tipo de lugar" in q_low or "tipo de local" in q_low or "instalação" in q_low or "instalacao" in q_low:
                    campo_encontrado = bool(lead_data.get("tipo_lugar") or lead_data.get("tipo_local"))
                elif "projeto luminotécnico" in q_low or "projeto luminotecnico" in q_low:
                    campo_encontrado = bool(lead_data.get("projeto_luminotecnico"))
                elif "tamanho" in q_low or "dimensões" in q_low or "dimensoes" in q_low:
                    campo_encontrado = bool(lead_data.get("tamanho") or lead_data.get("area") or lead_data.get("dimensoes"))
                elif "cnpj" in q_low:
                    campo_encontrado = bool(lead_data.get("cnpj"))
                elif "produto" in q_low:
                    campo_encontrado = bool(lead_data.get("produto"))
                elif "nome da empresa" in q_low or "empresa" in q_low:
                    campo_encontrado = bool(lead_data.get("empresa") or lead_data.get("company"))
                elif "nome" in q_low and "empresa" not in q_low:
                    campo_encontrado = bool(lead_data.get("nome") or lead_data.get("Contato"))
                elif "email" in q_low or "e-mail" in q_low:
                    campo_encontrado = bool(lead_data.get("email"))
                elif "telefone" in q_low or "whatsapp" in q_low:
                    campo_encontrado = bool(lead_data.get("telefone") or lead_data.get("phone"))
                elif "estrutura" in q_low:
                    campo_encontrado = bool(lead_data.get("estrutura"))
                elif "cultivo" in q_low:
                    campo_encontrado = bool(lead_data.get("cultivo"))
                elif "muda" in q_low:
                    campo_encontrado = bool(lead_data.get("muda_sem_cultivo"))
                elif "verba" in q_low:
                    campo_encontrado = bool(lead_data.get("tem_verba"))
                elif "projeto ou orçamento" in q_low or "projeto ou orcamento" in q_low:
                    campo_encontrado = bool(lead_data.get("tem_projeto_orcamento"))
                
                if not campo_encontrado:
                    perguntas_setor_completas = False
                    perguntas_faltando.append(q)
        
        # Dados completos = básicos + todas as perguntas do setor
        dados_completos = dados_basicos_completos and perguntas_setor_completas
        
        print(f"[FLUXO] 📋 ETAPA 1: VERIFICANDO DADOS COLETADOS")
        print(f"[FLUXO] {'─'*80}")
        print(f"[FLUXO] Setor: {sector or 'NÃO COLETADO'}")
        print(f"[FLUXO] Nome: {nome or 'NÃO COLETADO'}")
        print(f"[FLUXO] Email: {email or 'NÃO COLETADO'}")
        print(f"[FLUXO] Cidade: {cidade or 'NÃO COLETADO'}")
        print(f"[FLUXO] Telefone: {telefone or 'NÃO COLETADO'}")
        print(f"[FLUXO] Empresa: {empresa or 'NÃO COLETADO'}")
        print(f"[FLUXO] Dados básicos: {'✅ SIM' if dados_basicos_completos else '❌ NÃO'}")
        print(f"[FLUXO] Perguntas do setor: {'✅ TODAS' if perguntas_setor_completas else f'❌ FALTANDO: {perguntas_faltando}'}")
        print(f"[FLUXO] Dados completos: {'✅ SIM' if dados_completos else '❌ NÃO - AINDA FALTAM INFORMAÇÕES'}")
        print(f"[FLUXO] {'─'*80}\n")
        
        # 2. Se dados completos, processa: agendamento, Salesforce e Slack
        # IMPORTANTE: Só processa agendamento se TODOS os dados estiverem coletados (básicos + todas as perguntas do setor)
        if dados_completos:
            print(f"[FLUXO] ⚡ DADOS COMPLETOS - INICIANDO PROCESSAMENTO ⚡\n")
        else:
            print(f"[FLUXO] ⚠️ DADOS INCOMPLETOS - NÃO VAI AGENDAR AINDA ⚠️")
            if perguntas_faltando:
                print(f"[FLUXO] Faltam perguntas do setor: {perguntas_faltando}")
            print(f"[FLUXO] O sistema vai continuar coletando dados antes de agendar.\n")
        
        # 2.1. Roteia vendedor se necessário (sempre, mesmo se dados incompletos)
        print(f"[FLUXO] 👤 ETAPA 2.1: ROTEANDO VENDEDOR")
        print(f"[FLUXO] {'─'*80}")
        seller = seller_by_contact.get(contact_id) or {}
        if not seller or not seller.get("email"):
            ddd = _extract_ddd(contact_id)
            estado = lead_data.get("estado")
            print(f"[FLUXO] Buscando vendedor - DDD: {ddd}, Estado: {estado}, Setor: {sector}")
            seller = _route_seller(ddd, estado, sector)
            seller_by_contact[contact_id] = seller
            print(f"[FLUXO] ✅ Vendedor roteado: {seller.get('name', 'N/A')}")
            print(f"[FLUXO]    Email: {seller.get('email', 'N/A')}")
            print(f"[FLUXO]    Slack ID: {seller.get('slack_user_id', 'N/A')}")
        else:
            print(f"[FLUXO] ✅ Vendedor já roteado: {seller.get('name', 'N/A')}")
        print(f"[FLUXO] {'─'*80}\n")

        if not dados_completos:
            # BLOCO COMENTADO PARA EVITAR ROBOTIZAÇÃO
            # O prompt do sistema já orienta o LLM a coletar dados de forma natural.
            # Não forçaremos a pergunta aqui para permitir que o LLM use o contexto da conversa.
            
            # if perguntas_faltando:
            #     assistant_reply = f"Perfeito! {perguntas_faltando[0]}"
            # elif not sector:
            #     assistant_reply = "Perfeito! Só pra eu te direcionar certinho: é um projeto industrial, esportivo, cidades (iluminação pública) ou grow (estufa/viveiro)?"
            # elif not nome:
            #     assistant_reply = "Perfeito! Qual seu nome?"
            # elif not email:
            #     assistant_reply = "Perfeito! Qual seu melhor email?"
            # elif not cidade:
            #     assistant_reply = "Perfeito! Qual sua cidade e estado (ex: Curitiba-PR)?"
            # elif not empresa:
            #     assistant_reply = "Perfeito! Qual o nome da sua empresa?"

            # if conversation_history.get(contact_id):
            #     last_msg = conversation_history[contact_id][-1]
            #     if last_msg.get("role") == "assistant":
            #         last_msg["content"] = assistant_reply

            # return {"reply": _sanitize_reply(assistant_reply)}
            
            # Continua permitindo que o LLM (DeepSeek) responda normalmente
            pass
            
            # 2.2. Cria lead no CRM (Salesforce ou Pipedrive dependendo do setor)
            sector_low = (sector or "").lower()
            if sector_low == "cidades":
                print(f"[FLUXO] 📊 ETAPA 2.2: CRIANDO DEAL NO PIPEDRIVE (CIDADES)")
                print(f"[FLUXO] {'─'*80}")
                if contact_id not in pipedrive_deals_by_contact:
                    try:
                        seller = seller_by_contact.get(contact_id) or {}
                        pd_owner = seller.get("pipedrive_user_id")
                        
                        print(f"[FLUXO] Preparando dados para Pipedrive...")
                        print(f"[FLUXO] Owner ID: {pd_owner or 'N/A'}")
                        print(f"[FLUXO] Setor: {sector}")
                        print(f"[FLUXO] Criando deal...")
                        
                        deal_id = crm.create_person_and_deal_pipedrive({
                            "contact_name": lead_data.get("nome") or lead_data.get("contact_name"),
                            "email": lead_data.get("email"),
                            "phone": lead_data.get("telefone") or lead_data.get("phone"),
                            "sector": sector,
                            "company": lead_data.get("empresa") or lead_data.get("company"),
                            "source": "WhatsApp"
                        }, owner_id=pd_owner)
                        
                        if deal_id:
                            pipedrive_url = os.getenv('PIPEDRIVE_BASE_URL', 'https://silicon.pipedrive.com').rstrip('/')
                            pipedrive_link = f"{pipedrive_url}/deal/{deal_id}"
                            pipedrive_deals_by_contact[contact_id] = {
                                "id": deal_id,
                                "link": pipedrive_link
                            }
                            print(f"[FLUXO] ✅✅✅ DEAL CRIADO NO PIPEDRIVE ✅✅✅")
                            print(f"[FLUXO]    Deal ID: {deal_id}")
                            print(f"[FLUXO]    Link: {pipedrive_link}")
                        else:
                            print(f"[FLUXO] ❌ ERRO: Deal NÃO foi criado no Pipedrive (retornou None)")
                    except Exception as e:
                        print(f"[FLUXO] ❌ ERRO ao criar deal no Pipedrive: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    existing_deal = pipedrive_deals_by_contact[contact_id]
                    print(f"[FLUXO] ℹ️ Deal já existe no Pipedrive")
                    print(f"[FLUXO]    Deal ID: {existing_deal.get('id', 'N/A')}")
                    print(f"[FLUXO]    Link: {existing_deal.get('link', 'N/A')}")
            else:
                print(f"[FLUXO] 📊 ETAPA 2.2: CRIANDO LEAD NO SALESFORCE")
                print(f"[FLUXO] {'─'*80}")
                if contact_id not in salesforce_leads_by_contact:
                    try:
                        seller = seller_by_contact.get(contact_id) or {}
                        owner_id = seller.get("salesforce_user_id")
                        lead_data_prepared = _prepare_salesforce_data(lead_data, sector)
                        
                        # Gera resumo para o campo Coment_rio__c
                        summary_text = _summarize_data({"Setor": sector, **lead_data})
                        summary_comentario = f"Resumo da conversa via WhatsApp:\n\n{summary_text}"
                        
                        print(f"[FLUXO] Preparando dados para Salesforce...")
                        print(f"[FLUXO] Owner ID: {owner_id or 'N/A'}")
                        print(f"[FLUXO] Setor: {sector}")
                        print(f"[FLUXO] Criando lead...")
                        
                        lead_id = crm.create_lead_salesforce(lead_data_prepared, owner_id=owner_id, sector=sector, summary=summary_comentario)
                        if lead_id:
                            salesforce_leads_by_contact[contact_id] = {
                                "id": lead_id,
                                "link": _build_sf_lead_link(lead_id)
                            }
                            print(f"[FLUXO] ✅✅✅ LEAD CRIADO NO SALESFORCE ✅✅✅")
                            print(f"[FLUXO]    Lead ID: {lead_id}")
                            print(f"[FLUXO]    Link: {_build_sf_lead_link(lead_id)}")
                            print(f"[FLUXO]    Resumo adicionado no campo Coment_rio__c: ✅")
                        else:
                            print(f"[FLUXO] ❌ ERRO: Lead NÃO foi criado no Salesforce (retornou None)")
                    except Exception as e:
                        print(f"[FLUXO] ❌ ERRO ao criar lead no Salesforce: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    existing_lead = salesforce_leads_by_contact[contact_id]
                    print(f"[FLUXO] ℹ️ Lead já existe no Salesforce")
                    print(f"[FLUXO]    Lead ID: {existing_lead.get('id', 'N/A')}")
                    print(f"[FLUXO]    Link: {existing_lead.get('link', 'N/A')}")
            print(f"[FLUXO] {'─'*80}\n")
            
            # 2.3. Tenta agendar somente com dados completos.
            # Se o cliente pedir reunião antes disso, a Íris deve continuar qualificando.
            # Mantém defaults fora do bloco para a etapa 2.4 (Slack) não quebrar quando agendamento não roda.
            cliente_nao_quer_reuniao = actions.get("no_meeting_wanted", False)
            reuniao_urgente = actions.get("urgent_meeting", False)
            cliente_confirmou = actions.get("confirmed_scheduling", False)
            scheduling_success = False
            created_event = None
            start_dt_scheduled = None
            event_summary_text = ""
            schedule_requested = cliente_confirmou or actions.get("needs_scheduling")
            should_try_scheduling = bool(dados_completos and not cliente_nao_quer_reuniao)

            if schedule_requested and not dados_completos:
                print(f"[FLUXO] ⚠️ Cliente pediu agendamento antes de completar qualificação; mantendo coleta de dados.")
                faltas_curta = perguntas_faltando[:2]
                if faltas_curta:
                    perguntas_txt = " e ".join(faltas_curta)
                    assistant_reply = (
                        f"Consigo organizar a reunião com o Eduardo, mas antes preciso fechar duas informações: "
                        f"{perguntas_txt}"
                    )
                    if conversation_history.get(contact_id):
                        last_msg = conversation_history[contact_id][-1]
                        if last_msg.get("role") == "assistant":
                            last_msg["content"] = assistant_reply
            
            if should_try_scheduling:
                print(f"[FLUXO] 📅 ETAPA 2.3: VERIFICANDO AGENDAMENTO")
                print(f"[FLUXO] {'─'*80}")
                
                if reuniao_urgente:
                    print(f"[FLUXO] ⚠️ Reunião URGENTE detectada - NÃO vai agendar, apenas notificar consultor")
                    # Atualiza resposta do assistente para informar que consultor vai responder rápido
                    seller = seller_by_contact.get(contact_id) or {}
                    seller_name = seller.get("name", "nosso consultor")
                    assistant_reply = f"Entendi! Vou avisar o {seller_name} que você precisa de uma resposta rápida. Ele vai te chamar no WhatsApp o quanto antes."
                    # Atualiza histórico
                    if conversation_history.get(contact_id):
                        last_msg = conversation_history[contact_id][-1]
                        if last_msg.get("role") == "assistant":
                            last_msg["content"] = assistant_reply
                    # Não agenda, apenas notifica consultor (será feito na etapa 2.4)
                elif cliente_nao_quer_reuniao:
                    print(f"[FLUXO] ⚠️ Cliente disse que NÃO quer reunião - pulando agendamento")
                elif not cliente_confirmou:
                    print(f"[FLUXO] ⚠️ Cliente ainda NÃO confirmou o horário")
                # Se dados estão completos mas não há confirmação, busca slots automaticamente e sugere horário
                seller = seller_by_contact.get(contact_id) or {}
                if seller and seller.get("email"):
                    # Verifica se a última resposta da Íris já tem horário sugerido
                    last_assistant_msg = None
                    if conversation_history.get(contact_id):
                        for msg in reversed(conversation_history[contact_id]):
                            if msg.get("role") == "assistant":
                                last_assistant_msg = msg.get("content", "")
                                break
                    
                    # Verifica se já tem horário sugerido na resposta
                    ja_tem_horario = last_assistant_msg and any(word in last_assistant_msg.lower() for word in ["às", "horas", "hora", "10h", "11h", "12h", "13h", "14h", "15h", "16h", "17h", "18h", "9h", "8h", "que tal", "funciona pra você", "te serve"])
                    
                    # Verifica se cliente mencionou período (ex: "amanhã pela tarde") na mensagem atual
                    cliente_mentiou_periodo = any(phrase in user_text.lower() for phrase in ["amanhã", "amanha", "pela tarde", "pela manhã", "pela manha", "segunda", "terça", "quarta", "quinta", "sexta"])
                    
                    # Se NÃO tem horário sugerido OU cliente mencionou período, busca slots automaticamente e atualiza resposta
                    if not ja_tem_horario or cliente_mentiou_periodo:
                        print(f"[FLUXO] 🔍 Íris não sugeriu horário ainda - buscando slots automaticamente...")
                        try:
                            cal_id = seller.get("calendarId") or seller.get("calendar_id") or "primary"
                            impersonate_email = seller.get("email")
                            from datetime import datetime, timedelta, time
                            tzinfo = _get_tzinfo("America/Sao_Paulo")
                            now = datetime.now(tzinfo)
                            
                            # Tenta extrair período da mensagem do cliente (ex: "amanhã pela tarde")
                            user_msg_lower = user_text.lower()
                            start_from = None
                            
                            # Se cliente mencionou "amanhã pela tarde" ou similar
                            if "amanhã" in user_msg_lower or "amanha" in user_msg_lower:
                                tomorrow = now + timedelta(days=1)
                                if "tarde" in user_msg_lower:
                                    # Tarde: começa às 14h
                                    start_from = datetime.combine(tomorrow.date(), time(14, 0), tzinfo)
                                elif "manhã" in user_msg_lower or "manha" in user_msg_lower:
                                    # Manhã: começa às 9h
                                    start_from = datetime.combine(tomorrow.date(), time(9, 0), tzinfo)
                                else:
                                    # Apenas "amanhã": começa às 9h
                                    start_from = datetime.combine(tomorrow.date(), time(9, 0), tzinfo)
                            elif "tarde" in user_msg_lower:
                                # Apenas "tarde": começa às 14h de hoje se possível, ou amanhã
                                if now.hour < 17:
                                    start_from = datetime.combine(now.date(), time(14, 0), tzinfo)
                                    if start_from < now: # Se já passou das 14h, começa agora
                                        start_from = now + timedelta(minutes=30)
                                else:
                                    tomorrow = now + timedelta(days=1)
                                    start_from = datetime.combine(tomorrow.date(), time(14, 0), tzinfo)
                            elif "manhã" in user_msg_lower or "manha" in user_msg_lower:
                                # Apenas "manhã": começa às 9h de hoje se possível, ou amanhã
                                if now.hour < 11:
                                    start_from = datetime.combine(now.date(), time(9, 0), tzinfo)
                                    if start_from < now:
                                        start_from = now + timedelta(minutes=30)
                                else:
                                    tomorrow = now + timedelta(days=1)
                                    start_from = datetime.combine(tomorrow.date(), time(9, 0), tzinfo)
                            else:
                                # Padrão: amanhã às 9h
                                tomorrow = now + timedelta(days=1)
                                start_from = datetime.combine(tomorrow.date(), time(9, 0), tzinfo)
                            
                            print(f"[FLUXO] Buscando slot a partir de: {start_from.strftime('%d/%m/%Y %H:%M')}")
                            
                            slot = gcal_scheduler.find_next_slot(
                                calendar_id=cal_id,
                                start_from=start_from,
                                duration_minutes=30,
                                work_start=time(8, 0),
                                work_end=time(17, 30),
                                lunch_start=time(12, 0),
                                lunch_end=time(13, 0),
                                impersonate_email=impersonate_email
                            )
                            
                            if slot:
                                suggested_time = slot["start"]
                                when_txt = suggested_time.strftime('%d/%m/%Y às %H:%M')
                                seller_name = seller.get("name", "nosso consultor")
                                new_reply = f"Perfeito! Encontrei um horário livre. Que tal uma reunião com o {seller_name} {when_txt}? Funciona pra você?"
                                
                                # Atualiza a última resposta da Íris E também atualiza assistant_reply para ser retornada
                                if conversation_history.get(contact_id):
                                    for msg in reversed(conversation_history[contact_id]):
                                        if msg.get("role") == "assistant":
                                            msg["content"] = new_reply
                                            break
                                assistant_reply = new_reply  # Garante que a resposta atualizada seja retornada
                                print(f"[FLUXO] ✅ Resposta atualizada com horário sugerido: {when_txt}")
                            else:
                                print(f"[FLUXO] ⚠️ Nenhum slot disponível encontrado")
                                # Se não encontrou slot, sugere que cliente escolha
                                seller_name = seller.get("name", "nosso consultor")
                                new_reply = f"Deixa eu verificar a agenda do {seller_name}. Que dia e horário funcionam melhor pra você?"
                                if conversation_history.get(contact_id):
                                    for msg in reversed(conversation_history[contact_id]):
                                        if msg.get("role") == "assistant":
                                            msg["content"] = new_reply
                                            assistant_reply = new_reply
                                            break
                        except Exception as e:
                            print(f"[FLUXO] ❌ Erro ao buscar slots automaticamente: {e}")
                            import traceback
                            traceback.print_exc()
                    else:
                        print(f"[FLUXO] ✅ Íris já sugeriu horário - aguardando confirmação do cliente")
                    print(f"[FLUXO] Aguardando confirmação do cliente...")
                else:
                    # Tenta agendar (sempre, a menos que cliente tenha dito explicitamente que não quer)
                    print(f"[FLUXO] Tentando agendar reunião...")
                scheduling_info = actions.get("scheduling_info", {})
                
                try:
                    seller = seller_by_contact.get(contact_id) or {}
                    if seller and seller.get("email"):
                        cal_id = seller.get("calendarId") or seller.get("calendar_id") or "primary"
                        impersonate_email = seller.get("email")
                        
                        # Busca próximo slot disponível
                        from datetime import datetime, timedelta, time
                        tzinfo = _get_tzinfo("America/Sao_Paulo")
                        now = datetime.now(tzinfo)
                        
                        # PRIMEIRO: Tenta extrair horário da mensagem do CLIENTE (quando ele pede remarcação ou sugere horário)
                        # Se o cliente pediu um horário específico (ex: "poderia ser às 9:00"), usa esse horário
                        start_dt = None
                        cliente_sugeriu_horario_especifico = False
                        horario_sugerido_original = None
                        horario_da_iris = None
                        horario_do_cliente = None
                        
                        # Extrai horário da mensagem do CLIENTE primeiro (última mensagem do usuário)
                        import re
                        user_message_lower = user_text.lower()
                        # Procura por padrões como "9:00", "09:00", "9h", "às 9:00", "poderia ser às 9:00", "pode ser as 9:00"
                        # IMPORTANTE: Procura primeiro por padrões mais específicos que indicam que o cliente está PEDINDO um horário
                        cliente_hora_patterns = [
                            r'(?:poderia|pode|poderia ser|pode ser)\s+(?:ser\s+)?(?:às|as)\s+(\d{1,2}):?(\d{2})?',  # "poderia ser às 9:00", "pode ser as 9"
                            r'(\d{1,2}):(\d{2})',  # "9:00", "09:00"
                            r'às\s+(\d{1,2}):?(\d{2})?',  # "às 9:00", "às 9"
                            r'(\d{1,2})\s*h',  # "9h", "9 h"
                        ]
                        
                        for pattern in cliente_hora_patterns:
                            match = re.search(pattern, user_message_lower)
                            if match:
                                if ':' in pattern:
                                    hour = int(match.group(1))
                                    minute = int(match.group(2)) if len(match.groups()) > 1 and match.group(2) else 0
                                else:
                                    hour = int(match.group(1))
                                    minute = 0
                                
                                # Tenta extrair data também
                                date_match = re.search(r'(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?', user_message_lower)
                                if date_match:
                                    day = int(date_match.group(1))
                                    month = int(date_match.group(2))
                                    year_str = date_match.group(3)
                                    if year_str:
                                        year = int(year_str) if len(year_str) == 4 else (2000 + int(year_str))
                                    else:
                                        # Assume mesma data que a Íris sugeriu ou amanhã
                                        tomorrow = now + timedelta(days=1)
                                        year = tomorrow.year
                                    
                                    try:
                                        horario_do_cliente = datetime(year, month, day, hour, minute, tzinfo=tzinfo)
                                        print(f"[FLUXO] ✅ Horário extraído da mensagem do CLIENTE: {horario_do_cliente.strftime('%d/%m/%Y %H:%M')}")
                                        break
                                    except:
                                        pass
                                else:
                                    # Se não tem data, assume mesma data que a Íris sugeriu ou amanhã
                                    # Tenta pegar data da última mensagem da Íris
                                    if conversation_history.get(contact_id):
                                        for msg in reversed(conversation_history[contact_id]):
                                            if msg.get("role") == "assistant":
                                                iris_msg = msg.get("content", "")
                                                date_match_iris = re.search(r'(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?', iris_msg)
                                                if date_match_iris:
                                                    day = int(date_match_iris.group(1))
                                                    month = int(date_match_iris.group(2))
                                                    year_str = date_match_iris.group(3)
                                                    if year_str:
                                                        year = int(year_str) if len(year_str) == 4 else (2000 + int(year_str))
                                                    else:
                                                        tomorrow = now + timedelta(days=1)
                                                        year = tomorrow.year
                                                    
                                                    try:
                                                        horario_do_cliente = datetime(year, month, day, hour, minute, tzinfo=tzinfo)
                                                        print(f"[FLUXO] ✅ Horário extraído da mensagem do CLIENTE (com data da Íris): {horario_do_cliente.strftime('%d/%m/%Y %H:%M')}")
                                                        break
                                                    except:
                                                        pass
                                                break
                                    
                                    # Se não encontrou data, assume amanhã
                                    if not horario_do_cliente:
                                        tomorrow = now + timedelta(days=1)
                                        try:
                                            horario_do_cliente = datetime.combine(tomorrow.date(), time(hour, minute), tzinfo)
                                            print(f"[FLUXO] ✅ Horário extraído da mensagem do CLIENTE (assumindo amanhã): {horario_do_cliente.strftime('%d/%m/%Y %H:%M')}")
                                        except:
                                            pass
                                
                                if horario_do_cliente:
                                    break
                        
                        # PRIORIDADE 1: Se encontrou horário do cliente, usa ele (cliente pediu horário específico)
                        if horario_do_cliente:
                            start_dt = horario_do_cliente
                            cliente_sugeriu_horario_especifico = True
                            horario_sugerido_original = horario_do_cliente
                            print(f"[FLUXO] ✅ PRIORIDADE: Usando horário que o CLIENTE pediu: {horario_do_cliente.strftime('%d/%m/%Y %H:%M')}")
                        
                        # PRIORIDADE 2: Se não encontrou horário do cliente, tenta extrair da última mensagem da Íris (quando ela sugeriu)
                        # Isso é importante porque quando o cliente confirma "pode ser sim", 
                        # ele está confirmando o horário que a Íris sugeriu, não um novo horário
                        # MAS só se o cliente NÃO pediu um horário específico antes
                        if not start_dt and conversation_history.get(contact_id):
                            for msg in reversed(conversation_history[contact_id]):
                                if msg.get("role") == "assistant":
                                    iris_msg = msg.get("content", "")
                                    # Procura por padrões como "05/12/2025 às 10:00" ou "às 10:00" ou "10:00"
                                    import re
                                    # Padrão: DD/MM/YYYY às HH:MM ou DD/MM às HH:MM (com "às" ou "as")
                                    date_time_pattern = r'(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\s*(?:às|as)\s*(\d{1,2}):?(\d{2})?'
                                    match = re.search(date_time_pattern, iris_msg)
                                    if match:
                                        day = int(match.group(1))
                                        month = int(match.group(2))
                                        year_str = match.group(3)
                                        hour = int(match.group(4))
                                        minute = int(match.group(5)) if match.group(5) else 0
                                        
                                        # Determina o ano
                                        if year_str:
                                            if len(year_str) == 2:
                                                year = 2000 + int(year_str)
                                            else:
                                                year = int(year_str)
                                        else:
                                            # Se não tem ano, assume amanhã ou hoje
                                            target_date = now + timedelta(days=1)
                                            year = target_date.year
                                        
                                        try:
                                            horario_da_iris = datetime(year, month, day, hour, minute, tzinfo=tzinfo)
                                            print(f"[FLUXO] ✅ Horário extraído da mensagem da Íris: {horario_da_iris.strftime('%d/%m/%Y %H:%M')}")
                                            break
                                        except:
                                            pass
                                    
                                    # Se não encontrou com padrão completo, tenta apenas hora (ex: "às 10:00")
                                    if not horario_da_iris:
                                        hour_pattern = r'às\s+(\d{1,2}):?(\d{2})?'
                                        hour_match = re.search(hour_pattern, iris_msg)
                                        if hour_match:
                                            hour = int(hour_match.group(1))
                                            minute = int(hour_match.group(2)) if hour_match.group(2) else 0
                                            # Assume amanhã se não tem data
                                            target_date = now + timedelta(days=1)
                                            try:
                                                horario_da_iris = datetime.combine(target_date.date(), time(hour, minute), tzinfo)
                                                print(f"[FLUXO] ✅ Horário extraído da mensagem da Íris (apenas hora): {horario_da_iris.strftime('%d/%m/%Y %H:%M')}")
                                                break
                                            except:
                                                pass
                                    
                                    # Se encontrou horário da Íris, para de procurar
                                    if horario_da_iris:
                                        break
                        
                        # Se encontrou horário da Íris, usa ele (cliente confirmou esse horário)
                        # MAS só se não encontrou horário do cliente primeiro
                        if horario_da_iris and not horario_do_cliente:
                            start_dt = horario_da_iris
                            cliente_sugeriu_horario_especifico = False  # Cliente confirmou horário da Íris, não sugeriu novo
                            horario_sugerido_original = horario_da_iris
                            print(f"[FLUXO] ✅ Usando horário que a Íris sugeriu e o cliente confirmou: {horario_da_iris.strftime('%d/%m/%Y %H:%M')}")
                            # Se cliente já confirmou (disse "ok", "sim", etc), marca como não sugeriu horário específico
                            # para que o código agende direto sem perguntar novamente
                            if cliente_confirmou:
                                cliente_sugeriu_horario_especifico = False
                        elif horario_do_cliente:
                            # Cliente pediu horário específico - prioriza esse
                            start_dt = horario_do_cliente
                            cliente_sugeriu_horario_especifico = True
                            horario_sugerido_original = horario_do_cliente
                            print(f"[FLUXO] ✅ Usando horário que o CLIENTE pediu: {horario_do_cliente.strftime('%d/%m/%Y %H:%M')}")
                        
                        # Se não encontrou horário da Íris, tenta usar data/hora do scheduling_info (cliente sugeriu novo horário)
                        if not start_dt and scheduling_info.get("date") and scheduling_info.get("time"):
                            try:
                                # Melhora o parsing: extrai hora específica (ex: "16h" -> 16)
                                time_str = scheduling_info.get("time", "").lower()
                                hora_extraida = None
                                
                                # Tenta extrair hora de padrões como "16h", "16:00", "16", "9:00", "09:00"
                                import re
                                # Primeiro tenta formato completo "HH:MM" ou "H:MM"
                                hora_minuto_match = re.search(r'(\d{1,2}):(\d{2})', time_str)
                                if hora_minuto_match:
                                    hora_extraida = int(hora_minuto_match.group(1))
                                    minuto_extraido = int(hora_minuto_match.group(2))
                                else:
                                    # Se não encontrou formato completo, tenta apenas hora
                                    hora_match = re.search(r'(\d{1,2})[h:]?', time_str)
                                    if hora_match:
                                        hora_extraida = int(hora_match.group(1))
                                        minuto_extraido = 0
                                    else:
                                        hora_extraida = None
                                        minuto_extraido = 0
                                
                                # Parse da data
                                date_str = scheduling_info.get("date", "").lower()
                                if "amanhã" in date_str or "amanha" in date_str:
                                    target_date = now + timedelta(days=1)
                                elif "hoje" in date_str:
                                    target_date = now
                                else:
                                    from dateutil import parser as dateparser
                                    try:
                                        # Tenta parsear com locale pt_BR
                                        target_date = dateparser.parse(date_str, settings={'TIMEZONE': 'America/Sao_Paulo', 'PREFER_DATES_FROM': 'future'})
                                    except TypeError:
                                        # Se não suportar settings, tenta sem
                                        target_date = dateparser.parse(date_str)
                                    if target_date and target_date.tzinfo is None:
                                        target_date = target_date.replace(tzinfo=tzinfo)
                                
                                if target_date and hora_extraida is not None:
                                    # Usa o horário específico sugerido pelo cliente (com minutos se disponível)
                                    start_dt = datetime.combine(target_date.date(), time(hora_extraida, minuto_extraido), tzinfo)
                                    cliente_sugeriu_horario_especifico = True
                                    horario_sugerido_original = start_dt
                                    print(f"[FLUXO] ✅ Horário específico do cliente: {start_dt.strftime('%d/%m/%Y %H:%M')}")
                                else:
                                    # Fallback: tenta parsear com dateparser
                                    from dateutil import parser as dateparser
                                    combined = f"{scheduling_info['date']} {scheduling_info['time']}"
                                    try:
                                        start_dt = dateparser.parse(combined, settings={'TIMEZONE': 'America/Sao_Paulo', 'PREFER_DATES_FROM': 'future'})
                                    except TypeError:
                                        start_dt = dateparser.parse(combined)
                                    if start_dt and start_dt.tzinfo is None:
                                        start_dt = start_dt.replace(tzinfo=tzinfo)
                            except Exception as e:
                                print(f"[FLUXO] ⚠️ Erro ao parsear horário sugerido: {e}")
                                pass
                        
                        if not start_dt:
                            tomorrow = now + timedelta(days=1)
                            start_dt = datetime.combine(tomorrow.date(), time(14, 0), tzinfo)
                        
                        # Se cliente sugeriu horário específico, verifica disponibilidade PRIMEIRO
                        if cliente_sugeriu_horario_especifico and horario_sugerido_original:
                            end_dt_sugerido = horario_sugerido_original + timedelta(minutes=30)
                            is_available = gcal_scheduler.is_free(
                                cal_id,
                                horario_sugerido_original,
                                end_dt_sugerido,
                                impersonate_email=impersonate_email
                            )
                            
                            if is_available:
                                # Horário sugerido está disponível - usa ele!
                                print(f"[FLUXO] ✅ Horário sugerido pelo cliente está DISPONÍVEL: {horario_sugerido_original.strftime('%d/%m/%Y %H:%M')}")
                                start_dt = horario_sugerido_original
                                end_dt = end_dt_sugerido
                                slot = {"start": start_dt, "end": end_dt}
                            else:
                                # Horário não disponível - NÃO agenda automaticamente, precisa perguntar ao cliente primeiro
                                print(f"[FLUXO] ⚠️ Horário sugerido ({horario_sugerido_original.strftime('%H:%M')}) NÃO está disponível")
                                print(f"[FLUXO] ⚠️ Buscando próximo horário disponível para SUGERIR ao cliente...")
                                slot_alternativo = gcal_scheduler.find_next_slot(
                                    calendar_id=cal_id,
                                    start_from=horario_sugerido_original,
                                    duration_minutes=30,
                                    work_start=time(8, 0),
                                    work_end=time(17, 30),
                                    lunch_start=time(12, 0),
                                    lunch_end=time(13, 0),
                                    impersonate_email=impersonate_email
                                )
                                if slot_alternativo:
                                    horario_alternativo = slot_alternativo["start"]
                                    print(f"[FLUXO] ⚠️ Próximo horário disponível: {horario_alternativo.strftime('%d/%m/%Y %H:%M')}")
                                    # NÃO agenda ainda - precisa perguntar ao cliente primeiro
                                    # Atualiza a resposta do assistente para perguntar ao cliente
                                    seller_name = seller.get("name", "nosso consultor")
                                    horario_original_txt = horario_sugerido_original.strftime('%H:%M')
                                    horario_alt_txt = horario_alternativo.strftime('%d/%m/%Y às %H:%M')
                                    assistant_reply = f"Esse horário ({horario_original_txt}) não está disponível para o {seller_name}. Que tal remarcar a reunião para {horario_alt_txt}? Funciona pra você?"
                                    # Atualiza histórico
                                    if conversation_history.get(contact_id):
                                        last_msg = conversation_history[contact_id][-1]
                                        if last_msg.get("role") == "assistant":
                                            last_msg["content"] = assistant_reply
                                    # NÃO agenda ainda - aguarda confirmação do cliente
                                    slot = None
                                    scheduling_success = False
                                    created_event = None
                                    start_dt_scheduled = None
                                else:
                                    print(f"[FLUXO] ⚠️ Nenhum slot disponível encontrado")
                                    slot = None
                                    scheduling_success = False
                                    created_event = None
                                    start_dt_scheduled = None
                            
                            # Se o cliente sugeriu horário específico e está disponível, PERGUNTA primeiro antes de agendar
                            # MAS se o cliente já confirmou (disse "ok", "sim", etc), agenda direto
                            if slot and cliente_sugeriu_horario_especifico and not cliente_confirmou:
                                # Cliente pediu horário específico e está disponível - PERGUNTA antes de agendar (só se ainda não confirmou)
                                seller_name = seller.get("name", "nosso consultor")
                                when_txt = start_dt.strftime('%d/%m/%Y às %H:%M')
                                confirmation_msg = f"Perfeito! Esse horário está livre. Confirmo a reunião com o {seller_name} para {when_txt}? Funciona pra você?"
                                
                                assistant_reply = confirmation_msg
                                
                                # Atualiza histórico
                                if conversation_history.get(contact_id):
                                    last_msg = conversation_history[contact_id][-1]
                                    if last_msg.get("role") == "assistant":
                                        last_msg["content"] = confirmation_msg
                                
                                # NÃO agenda ainda - aguarda confirmação do cliente
                                slot = None
                                scheduling_success = False
                                created_event = None
                                start_dt_scheduled = None
                            elif slot or (cliente_confirmou and start_dt):
                                # Cliente confirmou horário sugerido pela Íris - agenda direto
                                # OU cliente confirmou e temos um horário válido
                                # Se não tem slot mas tem start_dt e cliente confirmou, usa start_dt direto
                                if not slot and start_dt:
                                    # Verifica se o horário está disponível antes de agendar
                                    is_available = gcal_scheduler.is_free(
                                        cal_id,
                                        start_dt,
                                        end_dt,
                                        impersonate_email=impersonate_email
                                    )
                                    if not is_available:
                                        # Horário não está disponível, busca próximo slot
                                        print(f"[FLUXO] ⚠️ Horário sugerido não está disponível, buscando próximo slot...")
                                        slot = gcal_scheduler.find_next_slot(
                                            calendar_id=cal_id,
                                            start_from=start_dt,
                                            duration_minutes=30,
                                            work_start=time(8, 0),
                                            work_end=time(17, 30),
                                            lunch_start=time(12, 0),
                                            lunch_end=time(13, 0),
                                            impersonate_email=impersonate_email
                                        )
                                        if slot:
                                            start_dt = slot["start"]
                                            end_dt = slot["end"]
                                            print(f"[FLUXO] ✅ Próximo slot disponível encontrado: {start_dt.strftime('%d/%m/%Y %H:%M')}")
                                
                                # Cria evento
                                event_summary = "Reunião Silicon"
                                description = _summarize_data({"Setor": sector, **lead_data})
                                attendees = [email] if email else []
                                location = cidade
                                
                                print(f"[FLUXO] Criando evento no Google Calendar...")
                                print(f"[FLUXO] Horário: {start_dt.strftime('%d/%m/%Y %H:%M')} - {end_dt.strftime('%H:%M')}")
                                created = gcal_scheduler.create_event(
                                    calendar_id=cal_id,
                                    summary=event_summary,
                                    description=description,
                                    start_dt=start_dt,
                                    end_dt=end_dt,
                                    attendees=attendees,
                                    location=location,
                                    impersonate_email=impersonate_email
                                )
                                
                                if created:
                                    when_txt = start_dt.strftime('%d/%m/%Y às %H:%M')
                                    calendar_link = created.get('htmlLink', '')
                                    event_id = created.get('id', 'N/A')
                                    print(f"[FLUXO] ✅✅✅ EVENTO CRIADO NO GOOGLE CALENDAR ✅✅✅")
                                    print(f"[FLUXO]    Event ID: {event_id}")
                                    print(f"[FLUXO]    Data/Hora: {when_txt}")
                                    print(f"[FLUXO]    Link: {calendar_link}")
                                    print(f"[FLUXO]    Participantes: {', '.join(attendees) if attendees else 'Nenhum'}")
                                    
                                    seller_name = seller.get("name", "nosso consultor")
                                    confirmation_msg = f"Perfeito! Agendei a reunião com o {seller_name} para {when_txt}. "
                                    if calendar_link:
                                        confirmation_msg += f"Link do evento: {calendar_link}. "
                                    confirmation_msg += "Você receberá um convite por email. Precisa de mais alguma coisa ou posso ajudar em algo mais?"
                                    
                                    assistant_reply = confirmation_msg
                                    
                                    # Marca que a reunião foi agendada para usar na notificação do Slack
                                    scheduling_success = True
                                    created_event = created
                                    start_dt_scheduled = start_dt
                                    event_summary_text = event_summary
                                    
                                    # Atualiza histórico
                                    if conversation_history.get(contact_id):
                                        last_msg = conversation_history[contact_id][-1]
                                        if last_msg.get("role") == "assistant":
                                            last_msg["content"] = confirmation_msg
                                else:
                                    print(f"[FLUXO] ❌ ERRO: Evento NÃO foi criado no Google Calendar")
                                    scheduling_success = False
                                    created_event = None
                                    start_dt_scheduled = None
                            else:
                                print(f"[FLUXO] ⚠️ Nenhum slot disponível encontrado no calendário")
                                scheduling_success = False
                                created_event = None
                                start_dt_scheduled = None
                        else:
                            # Cliente não sugeriu horário específico, busca próximo slot
                            print(f"[FLUXO] Buscando slot disponível a partir de: {start_dt.strftime('%d/%m/%Y %H:%M')}")
                            slot = gcal_scheduler.find_next_slot(
                                calendar_id=cal_id,
                                start_from=start_dt,
                                duration_minutes=30,
                                work_start=time(8, 0),
                                work_end=time(17, 30),
                                lunch_start=time(12, 0),
                                lunch_end=time(13, 0),
                                impersonate_email=impersonate_email
                            )
                            
                            if slot:
                                start_dt = slot["start"]
                                end_dt = slot["end"]
                                
                                # Cria evento
                                event_summary = "Reunião Silicon"
                                description = _summarize_data({"Setor": sector, **lead_data})
                                attendees = [email] if email else []
                                location = cidade
                                
                                print(f"[FLUXO] Slot encontrado: {start_dt.strftime('%d/%m/%Y %H:%M')} - {end_dt.strftime('%H:%M')}")
                                print(f"[FLUXO] Criando evento no Google Calendar...")
                                created = gcal_scheduler.create_event(
                                    calendar_id=cal_id,
                                    summary=event_summary,
                                    description=description,
                                    start_dt=start_dt,
                                    end_dt=end_dt,
                                    attendees=attendees,
                                    location=location,
                                    impersonate_email=impersonate_email
                                )
                                
                                if created:
                                    when_txt = start_dt.strftime('%d/%m/%Y às %H:%M')
                                    calendar_link = created.get('htmlLink', '')
                                    event_id = created.get('id', 'N/A')
                                    print(f"[FLUXO] ✅✅✅ EVENTO CRIADO NO GOOGLE CALENDAR ✅✅✅")
                                    print(f"[FLUXO]    Event ID: {event_id}")
                                    print(f"[FLUXO]    Data/Hora: {when_txt}")
                                    print(f"[FLUXO]    Link: {calendar_link}")
                                    print(f"[FLUXO]    Participantes: {', '.join(attendees) if attendees else 'Nenhum'}")
                                    
                                    seller_name = seller.get("name", "nosso consultor")
                                    confirmation_msg = f"Perfeito! Agendei a reunião com o {seller_name} para {when_txt}. "
                                    if calendar_link:
                                        confirmation_msg += f"Link do evento: {calendar_link}. "
                                    confirmation_msg += "Você receberá um convite por email. Precisa de mais alguma coisa ou posso ajudar em algo mais?"
                                    
                                    assistant_reply = confirmation_msg
                                    
                                    # Marca que a reunião foi agendada para usar na notificação do Slack
                                    scheduling_success = True
                                    created_event = created
                                    start_dt_scheduled = start_dt
                                    event_summary_text = event_summary
                                    
                                    # Atualiza histórico
                                    if conversation_history.get(contact_id):
                                        last_msg = conversation_history[contact_id][-1]
                                        if last_msg.get("role") == "assistant":
                                            last_msg["content"] = confirmation_msg
                                else:
                                    print(f"[FLUXO] ❌ ERRO: Evento NÃO foi criado no Google Calendar")
                                    scheduling_success = False
                                    created_event = None
                                    start_dt_scheduled = None
                            else:
                                print(f"[FLUXO] ⚠️ Nenhum slot disponível encontrado no calendário")
                                scheduling_success = False
                                created_event = None
                                start_dt_scheduled = None
                    else:
                        print(f"[FLUXO] ⚠️ Vendedor sem email, não é possível agendar")
                        print(f"[FLUXO]    Vendedor: {seller.get('name', 'N/A')}")
                        print(f"[FLUXO]    Email: {seller.get('email', 'N/A')}")
                except Exception as e:
                    print(f"[FLUXO] ❌ ERRO ao tentar agendar: {e}")
                    import traceback
                    traceback.print_exc()
            print(f"[FLUXO] {'─'*80}\n")
            
            # 2.4. Notifica no Slack SEMPRE (com status do agendamento)
            print(f"[FLUXO] 💬 ETAPA 2.4: NOTIFICANDO NO SLACK")
            print(f"[FLUXO] {'─'*80}")
            seller = seller_by_contact.get(contact_id) or {}
            print(f"[FLUXO] Seller encontrado: {seller.get('name', 'N/A')}")
            print(f"[FLUXO] Slack User ID: {seller.get('slack_user_id', 'N/A')}")
            print(f"[FLUXO] Email: {seller.get('email', 'N/A')}")
            
            # Prioriza email se slack_user_id for DM channel ID (começa com 'D')
            # DM channel IDs são mais propensos a falhar se o bot não tiver conversado antes
            slack_user_id = seller.get("slack_user_id")
            email = seller.get("email")
            
            # Se slack_user_id começa com 'D' (DM channel), tenta email primeiro
            if slack_user_id and str(slack_user_id).startswith('D') and email:
                print(f"[FLUXO] ⚠️ Slack User ID é DM channel (D...), tentando email primeiro")
                slack_id = email
                fallback_id = slack_user_id
            else:
                slack_id = slack_user_id or email
                fallback_id = email if slack_id == slack_user_id else slack_user_id
            
            print(f"[FLUXO] Slack ID a ser usado (primário): {slack_id}")
            print(f"[FLUXO] Slack ID fallback: {fallback_id}")
            if slack_id:
                # Garante que o lead foi criado no CRM antes de enviar mensagem
                # Para cidades: Pipedrive, para outros: Salesforce
                sf_link = ""
                pipedrive_link = ""
                if sector_low == "cidades":
                    pipedrive_link = (pipedrive_deals_by_contact.get(contact_id) or {}).get("link") or ""
                    pipedrive_deal_id = (pipedrive_deals_by_contact.get(contact_id) or {}).get("id") or None
                else:
                    sf_link = (salesforce_leads_by_contact.get(contact_id) or {}).get("link") or ""
                    sf_lead_id = (salesforce_leads_by_contact.get(contact_id) or {}).get("id") or None
                
                # Se o lead/deal não foi criado ainda, cria agora
                if sector_low == "cidades":
                    pipedrive_deal_id = (pipedrive_deals_by_contact.get(contact_id) or {}).get("id") or None
                    if not pipedrive_deal_id:
                        print(f"[FLUXO] ⚠️ Deal não foi criado ainda, criando agora no Pipedrive...")
                        try:
                            seller = seller_by_contact.get(contact_id) or {}
                            pd_owner = seller.get("pipedrive_user_id")
                            deal_id = crm.create_person_and_deal_pipedrive({
                                "contact_name": lead_data.get("nome") or lead_data.get("contact_name"),
                                "email": lead_data.get("email"),
                                "phone": lead_data.get("telefone") or lead_data.get("phone"),
                                "sector": sector,
                                "company": lead_data.get("empresa") or lead_data.get("company"),
                                "source": "WhatsApp"
                            }, owner_id=pd_owner)
                            if deal_id:
                                pipedrive_url = os.getenv('PIPEDRIVE_BASE_URL', 'https://silicon.pipedrive.com').rstrip('/')
                                pipedrive_link = f"{pipedrive_url}/deal/{deal_id}"
                                pipedrive_deals_by_contact[contact_id] = {
                                    "id": deal_id,
                                    "link": pipedrive_link
                                }
                                print(f"[FLUXO] ✅ Deal criado no Pipedrive (Deal ID: {deal_id})")
                            else:
                                print(f"[FLUXO] ❌ ERRO: Deal NÃO foi criado no Pipedrive")
                        except Exception as e:
                            print(f"[FLUXO] ❌ ERRO ao criar deal no Pipedrive: {e}")
                            import traceback
                            traceback.print_exc()
                    else:
                        pipedrive_link = (pipedrive_deals_by_contact.get(contact_id) or {}).get("link") or ""
                else:
                    if not sf_lead_id:
                        print(f"[FLUXO] ⚠️ Lead não foi criado ainda, criando agora no Salesforce...")
                        try:
                            seller = seller_by_contact.get(contact_id) or {}
                            owner_id = seller.get("salesforce_user_id")
                            lead_data_prepared = _prepare_salesforce_data(lead_data, sector)
                            summary_text = _summarize_data({"Setor": sector, **lead_data})
                            summary_comentario = f"Resumo da conversa via WhatsApp:\n\n{summary_text}"
                            
                            lead_id = crm.create_lead_salesforce(lead_data_prepared, owner_id=owner_id, sector=sector, summary=summary_comentario)
                            if lead_id:
                                salesforce_leads_by_contact[contact_id] = {
                                    "id": lead_id,
                                    "link": _build_sf_lead_link(lead_id)
                                }
                                sf_link = _build_sf_lead_link(lead_id)
                                sf_lead_id = lead_id
                                print(f"[FLUXO] ✅ Lead criado no Salesforce (Lead ID: {lead_id})")
                            else:
                                print(f"[FLUXO] ❌ ERRO: Lead NÃO foi criado no Salesforce")
                        except Exception as e:
                            print(f"[FLUXO] ❌ ERRO ao criar lead no Salesforce: {e}")
                            import traceback
                            traceback.print_exc()
                    else:
                        sf_link = (salesforce_leads_by_contact.get(contact_id) or {}).get("link") or ""
                
                summary = _summarize_data({"Setor": sector, **lead_data})
                
                # Monta mensagem única e completa usando a função unificada
                msg = _build_slack_message(
                    summary=summary,
                    scheduling_success=scheduling_success,
                    created_event=created_event,
                    start_dt_scheduled=start_dt_scheduled,
                    reuniao_urgente=reuniao_urgente,
                    cliente_nao_quer_reuniao=cliente_nao_quer_reuniao,
                    sf_link=sf_link,
                    pipedrive_link=pipedrive_link
                )
                
                # Logs detalhados
                if scheduling_success and created_event and start_dt_scheduled:
                    when_txt = start_dt_scheduled.strftime('%d/%m/%Y às %H:%M')
                    calendar_link = created_event.get('htmlLink', '')
                    event_id = created_event.get('id', 'N/A')
                    print(f"[FLUXO] Status: Reunião AGENDADA")
                    print(f"[FLUXO]    Event ID: {event_id}")
                    print(f"[FLUXO]    Data/Hora: {when_txt}")
                    print(f"[FLUXO]    Calendar Link: {calendar_link}")
                    print(f"[FLUXO]    Salesforce Link: {sf_link}")
                elif reuniao_urgente:
                    print(f"[FLUXO] Status: Reunião URGENTE - Consultor deve responder rápido")
                elif cliente_nao_quer_reuniao:
                    print(f"[FLUXO] Status: Cliente NÃO quis reunião")
                else:
                    print(f"[FLUXO] Status: Reunião NÃO agendada (sem slot ou erro)")
                
                print(f"[FLUXO] Slack ID: {slack_id}")
                print(f"[FLUXO] Salesforce Lead ID: {sf_lead_id}")
                print(f"[FLUXO] Mensagem a ser enviada ({len(msg)} caracteres):")
                print(f"[FLUXO] {msg}")
                print(f"[FLUXO] Enviando notificação...")
                
                try:
                    print(f"[FLUXO] 🔄 Tentando enviar notificação no Slack...")
                    result = slack_notify.notify_user(slack_id, msg)
                    if result:
                        print(f"[FLUXO] ✅✅✅ NOTIFICAÇÃO ENVIADA NO SLACK ✅✅✅")
                        print(f"[FLUXO]    Destinatário: {slack_id}")
                        print(f"[FLUXO]    Resultado: {result}")
                    else:
                        print(f"[FLUXO] ❌ ERRO: notify_user retornou False")
                        print(f"[FLUXO]    Tentando novamente com fallback...")
                        # Tenta novamente com fallback_id se disponível
                        if fallback_id and slack_id != fallback_id:
                            try:
                                print(f"[FLUXO] 🔄 Tentando com fallback: {fallback_id}")
                                result2 = slack_notify.notify_user(fallback_id, msg)
                                if result2:
                                    print(f"[FLUXO] ✅✅✅ NOTIFICAÇÃO ENVIADA NO SLACK (via fallback) ✅✅✅")
                                else:
                                    print(f"[FLUXO] ❌ Falha também com fallback")
                            except Exception as e2:
                                print(f"[FLUXO] ❌ ERRO ao tentar com fallback: {e2}")
                        else:
                            print(f"[FLUXO]    Verifique se SLACK_BOT_TOKEN está configurado no .env")
                            print(f"[FLUXO]    Verifique se o slack_id está correto: {slack_id}")
                except Exception as e:
                    print(f"[FLUXO] ❌ ERRO ao enviar notificação no Slack: {e}")
                    import traceback
                    traceback.print_exc()
                    # Tenta novamente com fallback_id
                    if fallback_id and slack_id != fallback_id:
                        try:
                            print(f"[FLUXO] 🔄 Tentando novamente com fallback: {fallback_id}")
                            result2 = slack_notify.notify_user(fallback_id, msg)
                            if result2:
                                print(f"[FLUXO] ✅✅✅ NOTIFICAÇÃO ENVIADA NO SLACK (via fallback, após erro) ✅✅✅")
                        except Exception as e2:
                            print(f"[FLUXO] ❌ ERRO também com fallback: {e2}")
                    import traceback
                    traceback.print_exc()
            else:
                print(f"[FLUXO] ⚠️ Nenhum slack_id disponível para notificar")
                print(f"[FLUXO]    Vendedor: {seller.get('name', 'N/A')}")
                print(f"[FLUXO]    Slack User ID: {seller.get('slack_user_id', 'N/A')}")
                print(f"[FLUXO]    Email: {seller.get('email', 'N/A')}")
            print(f"[FLUXO] {'─'*80}\n")
            
            print(f"[FLUXO] {'='*80}")
            print(f"[FLUXO] ===== FIM DO FLUXO PRINCIPAL =====")
            print(f"[FLUXO] {'='*80}\n")
        
        # Tratamento de "não quer reunião" apenas se dados não estão completos (caso especial)
        if actions.get("no_meeting_wanted") and actions.get("needs_slack_notification") and not dados_completos:
            # Notifica vendedor no Slack mesmo sem dados completos
            seller = seller_by_contact.get(contact_id) or {}
            # Tenta usar email se o slack_user_id começar com 'D' (DM ID pode não funcionar)
            slack_id = seller.get("slack_user_id")
            if slack_id and slack_id.startswith('D'):
                # Se for DM ID, tenta usar email como fallback
                email = seller.get("email")
                if email:
                    print(f"[FLUXO] ⚠️ Slack ID é DM channel (D...), tentando usar email: {email}")
                    slack_id = email
            elif not slack_id:
                slack_id = seller.get("email")
            if slack_id:
                lead_data = lead_data_by_contact.get(contact_id, {})
                summary = _summarize_data({"Setor": lead_data.get("setor", "N/A"), **lead_data})
                sf_link = (salesforce_leads_by_contact.get(contact_id) or {}).get("link") or ""
                # Para cidades: Pipedrive, para outros: Salesforce
                if sector_low == "cidades":
                    pipedrive_link = (pipedrive_deals_by_contact.get(contact_id) or {}).get("link") or ""
                    sf_link = ""
                else:
                    sf_link = (salesforce_leads_by_contact.get(contact_id) or {}).get("link") or ""
                    pipedrive_link = ""
                
                msg = _build_slack_message(
                    summary=summary,
                    scheduling_success=False,
                    created_event=None,
                    start_dt_scheduled=None,
                    reuniao_urgente=False,
                    cliente_nao_quer_reuniao=True,
                    sf_link=sf_link,
                    pipedrive_link=pipedrive_link
                )
                try:
                    slack_notify.notify_user(slack_id, msg)
                    print(f"[SLACK] Notificação enviada para {slack_id}")
                except Exception as e:
                    print(f"[SLACK] Erro ao enviar notificação: {e}")
        
        # Código duplicado removido - agora usa o novo fluxo acima (linhas 1742-1920)
        
        # Verifica se assistant_reply foi atualizado com confirmação de agendamento ou horário sugerido
        # (já deve ter sido atualizado no bloco de agendamento, mas verifica como fallback)
        if conversation_history.get(contact_id):
            last_msg = conversation_history[contact_id][-1]
            if last_msg.get("role") == "assistant":
                last_content = last_msg.get("content", "")
                # Se a última mensagem contém confirmação e assistant_reply não, atualiza
                if ("agendei" in last_content.lower() or "link do evento" in last_content.lower()):
                    if "agendei" not in assistant_reply.lower():
                        assistant_reply = last_content
                        print(f"[REPLY] ✅ Usando confirmação do histórico: {assistant_reply[:150]}...")
                # Se a última mensagem contém horário sugerido e assistant_reply não, atualiza
                elif any(word in last_content.lower() for word in ["que tal", "às", "horas", "hora", "funciona pra você", "te serve"]) and not any(word in assistant_reply.lower() for word in ["que tal", "às", "horas", "hora"]):
                    assistant_reply = last_content
                    print(f"[REPLY] ✅ Usando horário sugerido do histórico: {assistant_reply[:150]}...")
        
        # Log final antes de retornar
        if "agendei" in assistant_reply.lower():
            print(f"[REPLY] ✅✅✅ RETORNANDO RESPOSTA COM CONFIRMAÇÃO DE AGENDAMENTO ✅✅✅")
        else:
            print(f"[REPLY] ⚠️ Resposta não contém confirmação: {assistant_reply[:150]}...")
        
        print(f"[REPLY] 📤 Retornando resposta final ({len(assistant_reply)} chars)")
        return {"reply": _sanitize_reply(assistant_reply)}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] ========== ERRO NO /reply ==========")
        print(f"[ERROR] Tipo: {type(e).__name__}")
        print(f"[ERROR] Mensagem: {e}")
        import traceback
        error_trace = traceback.format_exc()
        print(f"[ERROR] Traceback completo:")
        print(error_trace)
        print(f"[ERROR] ====================================")
        # Retorna resposta de fallback ao invés de quebrar
        try:
            return {"reply": "Desculpe, ocorreu um erro ao processar sua mensagem. Tente novamente em alguns instantes."}
        except:
            raise HTTPException(status_code=500, detail=f"Erro ao processar mensagem: {e}")
    finally:
        try:
            _save_persisted_state()
            with _state_lock:
                run_lock_by_contact.pop(contact_id, None)
            print(f"[REPLY] Lock liberado para {contact_id}")
        except:
            pass


@app.get("/")
def health():
    return {"status": "ok", "model": "deepseek-chat", "system": "DeepSeek-V3.2-Speciale"}

@app.get("/lead-data/{contact_id}")
def get_lead_data(contact_id: str):
    """Retorna os dados coletados do lead"""
    data = lead_data_by_contact.get(contact_id, {})
    seller = seller_by_contact.get(contact_id)
    sf_lead = salesforce_leads_by_contact.get(contact_id)
    return {
        "contact_id": contact_id,
        "data": data,
        "seller": seller,
        "salesforce_lead": sf_lead,
        "has_salesforce_lead": contact_id in salesforce_leads_by_contact
    }


@app.post("/classify-outbound-reply")
def classify_outbound_reply(req: OutboundReplyClassificationRequest):
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message e obrigatoria")

    empresa = (req.empresa or "").strip()
    outbound_message = (req.outbound_message or "").strip()
    source_context = req.source_context or {}

    system = (
        "Voce classifica respostas inbound de prospeccao outbound da Silicon. "
        "Decida se a automacao deve ENCERRAR o contato ou CONTINUAR. "
        "ENCERRAR apenas quando a mensagem indicar claramente atendimento automatico, recepcao setorial, central de departamentos, "
        "menu de opcoes, autoatendimento corporativo ou outro fluxo que nao representa um lead qualificado do ICP grow. "
        "Se houver chance razoavel de a empresa ainda ser viveiro, estufa, produtor de mudas, agroflorestal ou negocio aderente ao ICP grow, escolha CONTINUAR. "
        "Nao encerre so porque existe atendimento automatico; avalie o texto e o contexto da empresa. "
        "Se decidir ENCERRAR, escreva suggested_reply em tom educado, respeitoso e profissional. "
        "Responda SOMENTE em JSON valido no formato: "
        "{\"decision\":\"continue|close\",\"confidence\":0,\"reason\":\"...\",\"suggested_reply\":\"...\"}."
    )
    user = (
        f"EMPRESA: {empresa or 'N/A'}\n"
        f"PHONE: {req.phone or 'N/A'}\n"
        f"MENSAGEM_OUTBOUND:\n{outbound_message or 'N/A'}\n\n"
        f"CONTEXTO_ADICIONAL:\n{json.dumps(source_context, ensure_ascii=False)}\n\n"
        f"MENSAGEM_DO_CLIENTE:\n{message}\n"
    )

    raw = ""
    try:
        raw = deepseek_client.chat_completion(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=220,
            top_p=0.9,
            stream=False,
            timeout=45,
        )
        data = json.loads((raw or "").strip())
        decision = str(data.get("decision") or "continue").strip().lower()
        if decision not in {"continue", "close"}:
            decision = "continue"
        confidence = int(data.get("confidence") or 0)
        confidence = max(0, min(100, confidence))
        reason = str(data.get("reason") or "").strip()
        suggested_reply = str(data.get("suggested_reply") or "").strip()
        return {
            "decision": decision,
            "confidence": confidence,
            "reason": reason,
            "suggested_reply": suggested_reply,
            "provider": "deepseek",
        }
    except Exception as e:
        print(f"[CLASSIFY_OUTBOUND_REPLY] fallback por erro: {e} | raw={raw[:400]}")
        return {
            "decision": "continue",
            "confidence": 0,
            "reason": "classification_failed_fallback_continue",
            "suggested_reply": "",
            "provider": "fallback",
        }


@app.post("/outbound/variant")
def outbound_variant(req: OutboundVariantRequest):
    base_message = (req.base_message or "").strip()
    empresa = (req.empresa or "").strip()
    city = (req.city or "").strip()
    state = (req.state or "").strip()
    tone = (req.tone or "aggressive").strip()
    constraints = req.constraints or {}
    max_chars = int(constraints.get("max_chars", 700))

    if not base_message and not empresa:
        raise HTTPException(status_code=400, detail="base_message ou empresa e obrigatorio")

    location_txt = ", ".join([p for p in [city, state] if p]) if (city or state) else "Brasil"
    system = (
        "Voce reescreve mensagens outbound para WhatsApp em pt-BR. "
        "Faca apenas variacoes leves de redacao (minimas). "
        "Preserve exatamente a intencao comercial, o assunto principal e o pedido final. "
        "Nao invente beneficios novos, nao mude o publico, nao mude a oferta e nao use emojis. "
        "Nao cite nome de empresa cliente. "
        "Use o nome do remetente como Eduardo e nunca use Iris/Íris."
    )
    user = (
        f"MENSAGEM_BASE:\n{base_message}\n\n"
        f"EMPRESA: {empresa or 'N/A'}\n"
        f"LOCAL: {location_txt}\n"
        f"TOM: {tone}\n"
        f"LIMITES: max_chars={max_chars}\n"
        "Reescreva com pequenas mudancas de abertura, conectivos e fechamento. "
        "Mantenha o texto curto e natural para WhatsApp. "
        "Nao transforme em copy longa e nao troque o foco da mensagem. "
        "Mantenha o remetente como Eduardo."
    )

    variant = ""
    provider = "fallback"
    try:
        variant = deepseek_client.chat_completion(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.35,
            max_tokens=220,
            top_p=0.95,
            stream=False,
            timeout=30,
        )
        if variant:
            provider = "deepseek"
    except Exception as e:
        print(f"[OUTBOUND_VARIANT] fallback por erro IA: {e}")

    if not variant:
        variant = _fallback_variant(base_message, empresa, city, state)
        provider = "fallback"

    variant = variant.strip()
    if len(variant) > max_chars:
        variant = variant[:max_chars].rstrip()

    return {
        "variant_message": variant,
        "provider": provider
    }

@app.post("/transcribe")
def transcribe(req: TranscribeRequest):
    use_openai = ensure_openai_or_offline()
    try:
        import base64
        from io import BytesIO
        b64 = (req.audio_b64 or "")
        if "," in b64 and b64.strip().startswith("data:"):
            b64 = b64.split(",", 1)[1]
        raw = base64.b64decode(b64)
        bio = BytesIO(raw)
        name = "audio.ogg"
        m = (req.audio_mime or "").lower()
        if "wav" in m:
            name = "audio.wav"
        elif "mp3" in m:
            name = "audio.mp3"
        elif "m4a" in m:
            name = "audio.m4a"
        bio.name = name
        try:
            r = client.audio.transcriptions.create(model="gpt-4o-mini-transcribe", file=bio)
        except Exception:
            r = client.audio.transcriptions.create(model="whisper-1", file=bio)
        txt = getattr(r, "text", "") or getattr(r, "output_text", "") or ""
        return {"text": txt}
    except Exception as e:
        return {"text": ""}
