import os
import sys
import json
from datetime import datetime, timedelta
from wpp_conversation import SlackNotifier, SalesforceClient, GoogleCalendarScheduler, next_business_day
def env_str(name, default=None):
    v = os.environ.get(name)
    return v if v is not None else default
def env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.lower() in ['1','true','yes','y']
def run(summary):
    phone = summary.get('phone')
    notifier = SlackNotifier(env_str('SLACK_BOT_TOKEN'), env_str('SLACK_USER_ID',''))
    scheduler = GoogleCalendarScheduler(env_str('GOOGLE_CREDENTIALS_PATH'), env_str('GOOGLE_IMPERSONATE_EMAIL'), env_str('GOOGLE_CALENDAR_ID','primary'))
    sfdc = SalesforceClient(env_str('SALESFORCE_USERNAME'), env_str('SALESFORCE_PASSWORD'), env_str('SALESFORCE_SECURITY_TOKEN'))
    meeting = None
    link = None
    if env_bool('ENABLE_SCHEDULING', True) and env_str('SCHEDULER_PROVIDER','google') == 'google':
        start = next_business_day(datetime.now().replace(hour=10, minute=0, second=0, microsecond=0))
        attendees = [env_str('DEFAULT_SELLER_EMAIL', env_str('GOOGLE_IMPERSONATE_EMAIL'))]
        meeting = scheduler.schedule('Reunião Silicon', 'Alinhamento inicial', start, 30, attendees)
        link = meeting.get('link') if meeting and meeting.get('ok') else None
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
    notifier.send(msg)
    return {'meeting':meeting,'lead':lead,'slack':'sent'}
def main():
    if len(sys.argv) >= 2:
        payload = sys.argv[1]
    else:
        payload = sys.stdin.read()
    try:
        summary = json.loads(payload)
    except Exception:
        print(json.dumps({'ok':False,'erro':'json_invalido'}))
        return
    res = run(summary)
    print(json.dumps({'ok':True,'result':res}, ensure_ascii=False))
if __name__ == '__main__':
    main()
