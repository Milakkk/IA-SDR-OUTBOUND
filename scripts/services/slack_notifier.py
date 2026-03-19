import os
import requests
def open_dm(user_id: str):
    tok = os.getenv('SLACK_BOT_TOKEN')
    if not tok:
        return {'ok':False,'erro':'token_ausente'}
    r = requests.post('https://slack.com/api/conversations.open', headers={'Authorization': f'Bearer {tok}'}, data={'users': user_id})
    try:
        j = r.json()
    except Exception:
        j = {'ok':False}
    return j
def post_message(channel: str, text: str):
    tok = os.getenv('SLACK_BOT_TOKEN')
    if not tok:
        return {'ok':False,'erro':'token_ausente'}
    r = requests.post('https://slack.com/api/chat.postMessage', headers={'Authorization': f'Bearer {tok}', 'Content-Type':'application/json; charset=utf-8'}, json={'channel': channel, 'text': text})
    try:
        j = r.json()
    except Exception:
        j = {'ok':False}
    return j
def notify_user(user_id: str, text: str):
    dm = open_dm(user_id)
    if not dm.get('ok'):
        return {'ok':False,'erro':dm}
    ch = dm.get('channel',{}).get('id')
    if not ch:
        return {'ok':False,'erro':'canal_indisponivel'}
    return post_message(ch, text)