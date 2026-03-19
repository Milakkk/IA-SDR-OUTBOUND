import os
import json
from datetime import datetime, timedelta, time
from typing import Optional, List, Dict, Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from dotenv import load_dotenv
from pathlib import Path


SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Load env from root .env (unified)
load_dotenv(dotenv_path=str(Path(__file__).resolve().parents[1] / '.env'))


def _load_credentials(impersonate_email: Optional[str] = None) -> service_account.Credentials:
    """Load Google service account credentials from env.

    Supports:
    - GOOGLE_CREDENTIALS_PATH: path to service account JSON file
    - GOOGLE_CREDENTIALS_JSON: JSON string of the service account
    - GOOGLE_CREDENTIALS_B64: base64-encoded JSON
    Optionally, impersonate_email or GOOGLE_IMPERSONATE_EMAIL: user to impersonate (domain-wide delegation)
    """
    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH")
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    creds_b64 = os.getenv("GOOGLE_CREDENTIALS_B64")
    impersonate = impersonate_email or os.getenv("GOOGLE_IMPERSONATE_EMAIL")

    info: Dict[str, Any] = {}

    repo_root = Path(__file__).resolve().parents[1]
    workspace_root = Path(__file__).resolve().parents[3]
    
    # Tenta encontrar o arquivo de credenciais em vários locais
    if creds_path:
        possible_paths = [
            creds_path,  # Caminho direto
            str(repo_root / creds_path),  # Raiz do projeto
            str(Path(__file__).resolve().parent / creds_path),  # Diretório ai-service
            str(workspace_root / creds_path),  # Workspace compartilhado
        ]
        for p in possible_paths:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    info = json.load(f)
                print(f"[CALENDAR] Credenciais carregadas de: {p}")
                break

    if not info:
        fallback_candidates = [
            workspace_root / "credentials.json",
            repo_root / "credentials.json",
        ]
        for p in fallback_candidates:
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    info = json.load(f)
                print(f"[CALENDAR] Credenciais carregadas de fallback: {p}")
                break
    
    if not info and creds_json:
        info = json.loads(creds_json)
    elif not info and creds_b64:
        import base64
        decoded = base64.b64decode(creds_b64).decode("utf-8")
        info = json.loads(decoded)
    
    if not info:
        raise RuntimeError("Credenciais Google não configuradas; defina GOOGLE_CREDENTIALS_PATH ou GOOGLE_CREDENTIALS_JSON/GOOGLE_CREDENTIALS_B64")

    base_creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    if impersonate:
        delegated = base_creds.with_subject(impersonate)
        try:
            # Valida a delegação antes de devolver as credenciais; se falhar, faz fallback.
            delegated.refresh(Request())
            print(f"[CALENDAR] [AUTH] Usando impersonation para: {impersonate}")
            return delegated
        except Exception as e:
            msg = str(e).lower()
            if "unauthorized_client" in msg or "not authorized" in msg:
                print(f"[CALENDAR] [AUTH] Impersonation não autorizada; fallback para conta de serviço direta ({e})")
                return base_creds
            raise

    print(f"[CALENDAR] [AUTH] Sem impersonation (usando conta de serviço direta)")
    return base_creds


def get_calendar_service(impersonate_email: Optional[str] = None):
    creds = _load_credentials(impersonate_email)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def is_free(calendar_id: str, start_dt: datetime, end_dt: datetime, impersonate_email: Optional[str] = None) -> bool:
    svc = get_calendar_service(impersonate_email)
    
    # Formata datas para o padrão RFC3339 com 'Z' (UTC) ou offset
    # Se já tem timezone, usa isoformat() normal que já inclui offset
    # Se não tem, assume UTC e adiciona 'Z'
    t_min = start_dt.isoformat()
    t_max = end_dt.isoformat()
    
    if start_dt.tzinfo is None:
        t_min += 'Z'
    if end_dt.tzinfo is None:
        t_max += 'Z'
        
    body = {
        "timeMin": t_min,
        "timeMax": t_max,
        "items": [{"id": calendar_id}],
    }
    
    try:
        fb = svc.freebusy().query(body=body).execute()
        busy = fb.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return len(busy) == 0
    except Exception as e:
        print(f"[SCHEDULER] Erro ao verificar freebusy: {e}")
        # Em caso de erro (ex: 400 Bad Request), assume que não está livre para evitar conflitos
        # ou tenta tratar erros específicos
        return False


def find_next_slot(
    calendar_id: str,
    start_from: datetime,
    duration_minutes: int = 30,
    work_start: time = time(8, 0),  # Horário de início: 8h
    work_end: time = time(17, 30),  # Horário de fim: 17:30
    lunch_start: time = time(12, 0),  # Início do almoço: 12h
    lunch_end: time = time(13, 0),  # Fim do almoço: 13h
    max_days_ahead: int = 14,
    impersonate_email: Optional[str] = None,
) -> Optional[Dict[str, datetime]]:
    """Find the next available slot within working hours using freeBusy.

    Iterates in 30-minute steps until a free interval is found.
    Avoids lunch time (12h-13h).
    """
    step = timedelta(minutes=30)
    svc = get_calendar_service(impersonate_email)
    current = start_from
    end_limit = start_from + timedelta(days=max_days_ahead)

    while current < end_limit:
        # Pula finais de semana
        if current.weekday() >= 5:  # Sábado (5) ou Domingo (6)
            next_monday = current.date() + timedelta(days=(7 - current.weekday()))
            current = datetime.combine(next_monday, work_start, tzinfo=current.tzinfo)
            continue
            
        # ensure within working hours; if before work_start, jump to work_start; if after work_end, next day
        if current.time() < work_start:
            current = datetime.combine(current.date(), work_start, tzinfo=current.tzinfo)
        elif current.time() >= work_end:
            next_day = current.date() + timedelta(days=1)
            current = datetime.combine(next_day, work_start, tzinfo=current.tzinfo)
            continue
        
        # Pula horário de almoço (12h-13h)
        if lunch_start <= current.time() < lunch_end:
            current = datetime.combine(current.date(), lunch_end, tzinfo=current.tzinfo)
            continue

        candidate_end = current + timedelta(minutes=duration_minutes)
        
        # Se a reunião termina após o expediente, pula para próximo dia
        if candidate_end.time() > work_end:
            next_day = current.date() + timedelta(days=1)
            current = datetime.combine(next_day, work_start, tzinfo=current.tzinfo)
            continue
        
        # Se a reunião invade o horário de almoço, pula para depois do almoço
        if current.time() < lunch_start and candidate_end.time() > lunch_start:
            current = datetime.combine(current.date(), lunch_end, tzinfo=current.tzinfo)
            continue

        # Formata datas corretamente para evitar 400 Bad Request
        t_min = current.isoformat()
        t_max = candidate_end.isoformat()
        
        if current.tzinfo is None:
            t_min += 'Z'
        if candidate_end.tzinfo is None:
            t_max += 'Z'

        body = {
            "timeMin": t_min,
            "timeMax": t_max,
            "items": [{"id": calendar_id}],
        }
        fb = svc.freebusy().query(body=body).execute()
        busy = fb.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        if len(busy) == 0:
            return {"start": current, "end": candidate_end}

        current += step

    return None


def find_next_slots(
    calendar_id: str,
    start_from: datetime,
    count: int = 3,
    duration_minutes: int = 30,
    work_start: time = time(8, 0),
    work_end: time = time(17, 30),
    lunch_start: time = time(12, 0),
    lunch_end: time = time(13, 0),
    max_days_ahead: int = 14,
    impersonate_email: Optional[str] = None,
) -> List[Dict[str, datetime]]:
    """Find the next 'count' available slots within working hours."""
    slots = []
    step = timedelta(minutes=30)
    svc = get_calendar_service(impersonate_email)
    current = start_from
    end_limit = start_from + timedelta(days=max_days_ahead)
    
    print(f"[SCHEDULER] find_next_slots: Buscando {count} slots para {calendar_id} a partir de {start_from} (Impersonate: {impersonate_email})")
    print(f"[SCHEDULER] Config: work={work_start}-{work_end}, lunch={lunch_start}-{lunch_end}, days={max_days_ahead}")

    # Evita loop infinito
    attempts = 0
    max_attempts = 300  # ~15 dias úteis de busca em steps de 30min

    while len(slots) < count and current < end_limit and attempts < max_attempts:
        attempts += 1
        
        # Pula finais de semana
        if current.weekday() >= 5:  # Sábado (5) ou Domingo (6)
            next_monday = current.date() + timedelta(days=(7 - current.weekday()))
            current = datetime.combine(next_monday, work_start, tzinfo=current.tzinfo)
            continue
            
        # Ajusta horário de trabalho
        if current.time() < work_start:
            current = datetime.combine(current.date(), work_start, tzinfo=current.tzinfo)
        elif current.time() >= work_end:
            next_day = current.date() + timedelta(days=1)
            current = datetime.combine(next_day, work_start, tzinfo=current.tzinfo)
            continue
        
        # Pula almoço
        if lunch_start <= current.time() < lunch_end:
            current = datetime.combine(current.date(), lunch_end, tzinfo=current.tzinfo)
            continue

        candidate_end = current + timedelta(minutes=duration_minutes)
        
        # Se termina após expediente, pula para dia seguinte
        if candidate_end.time() > work_end:
            next_day = current.date() + timedelta(days=1)
            current = datetime.combine(next_day, work_start, tzinfo=current.tzinfo)
            continue
        
        # Se invade almoço, pula para tarde
        if current.time() < lunch_start and candidate_end.time() > lunch_start:
            current = datetime.combine(current.date(), lunch_end, tzinfo=current.tzinfo)
            continue

        # Verifica disponibilidade
        # Formata datas corretamente para evitar 400 Bad Request
        t_min = current.isoformat()
        t_max = candidate_end.isoformat()
        
        if current.tzinfo is None:
            t_min += 'Z'
        if candidate_end.tzinfo is None:
            t_max += 'Z'

        body = {
            "timeMin": t_min,
            "timeMax": t_max,
            "items": [{"id": calendar_id}],
        }
        try:
            fb = svc.freebusy().query(body=body).execute()
            busy = fb.get("calendars", {}).get(calendar_id, {}).get("busy", [])
            
            if len(busy) == 0:
                slots.append({"start": current, "end": candidate_end})
                # Pula 1h após um slot encontrado para dar opções variadas
                current += timedelta(minutes=60)
            else:
                current += step
        except Exception as e:
            print(f"[SCHEDULER] Erro ao buscar freebusy: {e}")
            current += step

    print(f"[SCHEDULER] Encontrados {len(slots)} slots: {[s['start'].isoformat() for s in slots]}")
    return slots


def create_event(
    calendar_id: str,
    summary: str,
    description: Optional[str],
    start_dt: datetime,
    end_dt: datetime,
    attendees: Optional[List[str]] = None,
    location: Optional[str] = None,
    impersonate_email: Optional[str] = None,
) -> Dict[str, Any]:
    print(f"[CALENDAR] [create_event] Iniciando criação de evento...")
    print(f"[CALENDAR] [create_event]   - Calendar ID: {calendar_id}")
    print(f"[CALENDAR] [create_event]   - Impersonate Email: {impersonate_email or 'N/A'}")
    print(f"[CALENDAR] [create_event]   - Summary: {summary}")
    print(f"[CALENDAR] [create_event]   - Start: {start_dt.isoformat()}")
    print(f"[CALENDAR] [create_event]   - End: {end_dt.isoformat()}")
    try:
        svc = get_calendar_service(impersonate_email)
        print(f"[CALENDAR] [create_event] Serviço de calendário obtido com sucesso")
        event_body: Dict[str, Any] = {
            "summary": summary,
            "description": description or "",
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()},
        }
        if attendees:
            event_body["attendees"] = [{"email": e} for e in attendees]
            print(f"[CALENDAR] [create_event]   - Attendees: {attendees}")
        if location:
            event_body["location"] = location
            print(f"[CALENDAR] [create_event]   - Location: {location}")
        
        # Adiciona Google Meet para reunião online
        event_body["conferenceData"] = {
            "createRequest": {
                "requestId": f"meet-{start_dt.isoformat()}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"}
            }
        }
        print(f"[CALENDAR] [create_event]   - Google Meet: Habilitado")

        print(f"[CALENDAR] [create_event] Chamando API do Google Calendar...")
        created = svc.events().insert(calendarId=calendar_id, body=event_body, sendUpdates="all", conferenceDataVersion=1).execute()
        print(f"[CALENDAR] [create_event] [OK] Evento criado!")
        print(f"[CALENDAR] [create_event]   - Event ID: {created.get('id', 'N/A')}")
        print(f"[CALENDAR] [create_event]   - Link: {created.get('htmlLink', 'N/A')}")
        return created
    except Exception as e:
        print(f"[CALENDAR] [create_event] [ERRO] Erro ao criar evento: {e}")
        import traceback
        traceback.print_exc()
        raise


def list_events(
    calendar_id: str,
    time_min: datetime,
    time_max: datetime,
    query: Optional[str] = None,
    impersonate_email: Optional[str] = None,
) -> List[Dict[str, Any]]:
    svc = get_calendar_service(impersonate_email)
    page_token = None
    items: List[Dict[str, Any]] = []
    tmn = time_min.isoformat() + ("Z" if time_min.tzinfo is None else "")
    tmx = time_max.isoformat() + ("Z" if time_max.tzinfo is None else "")
    while True:
        resp = svc.events().list(
            calendarId=calendar_id,
            timeMin=tmn,
            timeMax=tmx,
            q=query,
            singleEvents=True,
            orderBy="startTime",
            pageToken=page_token,
        ).execute()
        items.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


def delete_event(calendar_id: str, event_id: str, impersonate_email: Optional[str] = None) -> None:
    svc = get_calendar_service(impersonate_email)
    svc.events().delete(calendarId=calendar_id, eventId=event_id, sendUpdates="all").execute()
