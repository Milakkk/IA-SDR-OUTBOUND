import os
import sys
import re
import json
import time
import requests
from datetime import datetime, timedelta

def env_str(name, default=None):
    v = os.environ.get(name)
    return v if v is not None else default

def env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.lower() in ['1','true','yes','y']

def parse_allowed_contacts(v):
    if not v:
        return []
    parts = [p.strip() for p in v.split(',') if p.strip()]
    return parts

class ConsoleAdapter:
    def send(self, to, text):
        print({'to':to,'send':text})
    def recv(self, prompt):
        return input(prompt)

class SlackNotifier:
    def __init__(self, bot_token, user_id):
        self.bot_token = bot_token
        self.user_id = user_id
    def send(self, text):
        if not self.bot_token or not self.user_id:
            return {'ok':False,'error':'missing_slack_config'}
        h = {'Authorization':f'Bearer {self.bot_token}','Content-Type':'application/json'}
        r1 = requests.post('https://slack.com/api/conversations.open', headers=h, data=json.dumps({'users':self.user_id}))
        j1 = r1.json()
        if not j1.get('ok'):
            return {'ok':False,'error':j1}
        ch = j1.get('channel',{}).get('id')
        r2 = requests.post('https://slack.com/api/chat.postMessage', headers=h, data=json.dumps({'channel':ch,'text':text}))
        j2 = r2.json()
        return j2

class SalesforceClient:
    def __init__(self, username, password, security_token):
        self.username = username
        self.password = password
        self.security_token = security_token
        self.session_id = None
        self.instance_url = None
        self.api_version = env_str('SALESFORCE_API_VERSION','59.0')
    def login(self):
        if not self.username or not self.password or not self.security_token:
            return {'ok':False,'error':'missing_salesforce_config'}
        url = f'https://login.salesforce.com/services/Soap/u/{self.api_version}'
        body = """<?xml version="1.0" encoding="utf-8"?>
<env:Envelope xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">
  <env:Body>
    <n1:login xmlns:n1="urn:partner.soap.sforce.com">
      <n1:username>""" + self.username + """</n1:username>
      <n1:password>""" + (self.password + self.security_token) + """</n1:password>
    </n1:login>
  </env:Body>
</env:Envelope>"""
        headers = {'Content-Type':'text/xml; charset=UTF-8','SOAPAction':'login'}
        r = requests.post(url, data=body.encode('utf-8'), headers=headers)
        t = r.text
        m_sid = re.search(r'<sessionId>([^<]+)</sessionId>', t)
        m_url = re.search(r'<serverUrl>(https?://[^<]+)</serverUrl>', t)
        if not m_sid or not m_url:
            return {'ok':False,'error':'login_failed','status_code':r.status_code,'text':t}
        self.session_id = m_sid.group(1)
        su = m_url.group(1)
        b = su.split('/services/Soap')[0]
        self.instance_url = b
        return {'ok':True,'instance_url':self.instance_url}
    def create_lead(self, data):
        if not self.session_id or not self.instance_url:
            lr = self.login()
            if not lr.get('ok'):
                return lr
        url = f'{self.instance_url}/services/data/v{self.api_version}/sobjects/Lead'
        h = {'Authorization':f'Bearer {self.session_id}','Content-Type':'application/json'}
        r = requests.post(url, headers=h, data=json.dumps(data))
        return r.json()

class GoogleCalendarScheduler:
    def __init__(self, credentials_path, impersonate_email, calendar_id):
        self.credentials_path = credentials_path
        self.impersonate_email = impersonate_email
        self.calendar_id = calendar_id or 'primary'
    def schedule(self, title, description, start_dt, duration_minutes, attendees):
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
        except Exception:
            return {'ok':False,'error':'missing_google_libs'}
        scopes = ['https://www.googleapis.com/auth/calendar']
        if not self.credentials_path or not os.path.exists(self.credentials_path):
            return {'ok':False,'error':'missing_credentials_file'}
        creds = Credentials.from_service_account_file(self.credentials_path, scopes=scopes)
        if self.impersonate_email:
            creds = creds.with_subject(self.impersonate_email)
        svc = build('calendar','v3',credentials=creds)
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        ev = {
            'summary': title,
            'description': description,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'attendees': [{'email': e} for e in attendees if e]
        }
        created = svc.events().insert(calendarId=self.calendar_id, body=ev).execute()
        return {'ok':True,'id':created.get('id'),'link':created.get('htmlLink')}

def parse_yesno(text):
    s = text.strip().lower()
    if any(k in s for k in ['sim','tenho','possuo','tem','existe']):
        return True
    if any(k in s for k in ['nao','não','não tenho','nao tenho','não possuo','nao possuo']):
        return False
    return None

CULTURES = ['eucalipto','pinus','café','cafe','cana','microverde','hortaliças','hortalicas']

def parse_cultures(text):
    s = text.strip().lower()
    found = []
    for c in CULTURES:
        if c in s:
            if c == 'café':
                found.append('café')
            elif c == 'hortalicas':
                found.append('hortaliças')
            else:
                found.append(c)
    return list(dict.fromkeys(found))

def parse_seedling(text):
    s = text.strip().lower()
    if any(k in s for k in ['muda','sem cultivo']):
        return True
    if any(k in s for k in ['não precisa','nao precisa','adulto','cultivo']):
        return False
    return None

def next_business_day(dt):
    d = dt + timedelta(days=1)
    while d.weekday() >= 5:
        d = d + timedelta(days=1)
    return d

def run_contact(adapter, phone, notifier, scheduler, sfdc, dry_run):
    adapter.send(phone, 'Olá, tudo bem? Aqui é o assistente da Silicon. Você possui estrutura de casa de vegetação ou estufa?')
    r1 = adapter.recv('Resposta 1: ')
    has_struct = parse_yesno(r1)
    adapter.send(phone, 'Quais culturas dentro da matriz de testes? Eucalipto, Pinus, Café, Cana, Microverde, Hortaliças.')
    r2 = adapter.recv('Resposta 2: ')
    cultures = parse_cultures(r2)
    adapter.send(phone, 'Precisa ser muda (sem cultivo)?')
    r3 = adapter.recv('Resposta 3: ')
    needs_seedling = parse_seedling(r3)
    summary = {
        'phone': phone,
        'estrutura': has_struct,
        'cultivos': cultures,
        'muda': needs_seedling
    }
    meeting = None
    lead = None
    link = None
    if not dry_run and env_bool('ENABLE_SCHEDULING', True) and env_str('SCHEDULER_PROVIDER','google') == 'google':
        start = next_business_day(datetime.now().replace(hour=10, minute=0, second=0, microsecond=0))
        attendees = [env_str('DEFAULT_SELLER_EMAIL', env_str('GOOGLE_IMPERSONATE_EMAIL'))]
        meeting = scheduler.schedule('Reunião Silicon', 'Alinhamento inicial', start, 30, attendees)
        link = meeting.get('link') if meeting and meeting.get('ok') else None
    if not dry_run:
        lead_data = {
            'LastName': 'Prospect WhatsApp',
            'Company': 'Prospect',
            'Phone': phone,
            'Status': 'Open - Not Contacted',
            'Description': json.dumps(summary, ensure_ascii=False),
        }
        owner_id = env_str('SF_OWNER_ID', env_str('SALESFORCE_OWNER_ID', ''))
        if owner_id:
            lead_data['OwnerId'] = owner_id
        lead = sfdc.create_lead(lead_data)
    msg = 'Novo prospect via WhatsApp\n' + json.dumps(summary, ensure_ascii=False)
    if link:
        msg += f"\nAgenda: {link}"
    if not dry_run:
        notifier.send(msg)
    else:
        print({'slack_preview':msg})
    return {'summary':summary,'meeting':meeting,'lead':lead}

def main():
    dry_run = env_bool('DRY_RUN', True)
    allowed = parse_allowed_contacts(env_str('ALLOWED_CONTACTS'))
    if not allowed:
        print({'erro':'sem_contatos'})
        return
    adapter = ConsoleAdapter()
    notifier = SlackNotifier(env_str('SLACK_BOT_TOKEN'), env_str('SLACK_USER_ID',''))
    scheduler = GoogleCalendarScheduler(env_str('GOOGLE_CREDENTIALS_PATH'), env_str('GOOGLE_IMPERSONATE_EMAIL'), env_str('GOOGLE_CALENDAR_ID','primary'))
    sfdc = SalesforceClient(env_str('SALESFORCE_USERNAME'), env_str('SALESFORCE_PASSWORD'), env_str('SALESFORCE_SECURITY_TOKEN'))
    for phone in allowed:
        run_contact(adapter, phone, notifier, scheduler, sfdc, dry_run)

if __name__ == '__main__':
    main()
