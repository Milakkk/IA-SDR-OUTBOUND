import os
import datetime
import json
from typing import List, Optional, Dict
def _load_creds():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except Exception as e:
        return None, None, {'erro':'dependencia_ausente','detalhe':str(e)}
    path = os.getenv('GOOGLE_CREDENTIALS_PATH')
    if not path or not os.path.exists(path):
        return None, None, {'erro':'credenciais_ausentes','path':path}
    scopes = ['https://www.googleapis.com/auth/calendar']
    try:
        creds = service_account.Credentials.from_service_account_file(path, scopes=scopes)
        imp = os.getenv('GOOGLE_IMPERSONATE_EMAIL')
        if imp:
            creds = creds.with_subject(imp)
        svc = build('calendar', 'v3', credentials=creds)
        return creds, svc, None
    except Exception as e:
        return None, None, {'erro':'falha_carregar_credenciais','detalhe':str(e)}
def schedule_meeting(summary: str, attendees: List[str], start_dt: Optional[datetime.datetime] = None, duration_minutes: int = 30, timezone: str = 'America/Sao_Paulo') -> Dict:
    _, svc, err = _load_creds()
    if err:
        return {'ok':False,'erro':err}
    cal_id = os.getenv('GOOGLE_CALENDAR_ID','primary')
    now = datetime.datetime.now(datetime.timezone.utc)
    if not start_dt:
        local = datetime.datetime.now()
        base = local + datetime.timedelta(days=1)
        start_local = base.replace(hour=10, minute=0, second=0, microsecond=0)
        start_dt = start_local
    end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
    ev = {
        'summary': summary,
        'start': {'dateTime': start_dt.isoformat(), 'timeZone': timezone},
        'end': {'dateTime': end_dt.isoformat(), 'timeZone': timezone},
    }
    if attendees:
        ev['attendees'] = [{'email': a} for a in attendees if a]
    try:
        created = svc.events().insert(calendarId=cal_id, body=ev).execute()
        return {'ok':True,'event':created}
    except Exception as e:
        return {'ok':False,'erro':{'erro':'falha_criar_evento','detalhe':str(e)}}