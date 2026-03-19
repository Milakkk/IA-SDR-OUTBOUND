import os
from typing import Optional

import requests
from dotenv import load_dotenv
from pathlib import Path


def notify_user(slack_user_id: str, text: str) -> bool:
    load_dotenv(dotenv_path=str(Path(__file__).resolve().parents[1] / '.env'))
    token = os.getenv('SLACK_BOT_TOKEN')
    print(f"[SLACK_NOTIFY] Iniciando notificação...")
    print(f"[SLACK_NOTIFY] Slack User ID: {slack_user_id}")
    print(f"[SLACK_NOTIFY] Token configurado: {'Sim' if token else 'NÃO'}")
    print(f"[SLACK_NOTIFY] Texto ({len(text) if text else 0} caracteres): {text[:100]}...")
    if not token or not slack_user_id or not text:
        print(f"[SLACK_NOTIFY] [ERRO] Validação falhou: token={bool(token)}, slack_id={bool(slack_user_id)}, text={bool(text)}")
        return False

    try:
        channel = None
        sid = str(slack_user_id)
        if sid.startswith('D'):
            # ID que começa com 'D' é um DM channel ID
            # Mas pode não estar acessível se o bot não tiver conversado com o usuário antes
            # Tenta usar diretamente primeiro
            channel = sid
            print(f"[SLACK_NOTIFY] Usando DM channel ID diretamente: {sid}")
            # Se falhar, vamos tentar buscar o user ID do email
        elif sid.startswith('U'):
            r_open = requests.post(
                'https://slack.com/api/conversations.open',
                headers={'Authorization': f'Bearer {token}'},
                data={'users': sid},
                timeout=15
            )
            r_open.raise_for_status()
            channel = (r_open.json().get('channel') or {}).get('id')
        elif '@' in sid:
            r_lookup = requests.get(
                'https://slack.com/api/users.lookupByEmail',
                headers={'Authorization': f'Bearer {token}'},
                params={'email': sid},
                timeout=15
            )
            r_lookup.raise_for_status()
            uid = (r_lookup.json().get('user') or {}).get('id')
            if not uid:
                return False
            r_open = requests.post(
                'https://slack.com/api/conversations.open',
                headers={'Authorization': f'Bearer {token}'},
                data={'users': uid},
                timeout=15
            )
            r_open.raise_for_status()
            channel = (r_open.json().get('channel') or {}).get('id')
        else:
            return False

        if not channel:
            return False

        r_msg = requests.post(
            'https://slack.com/api/chat.postMessage',
            headers={'Authorization': f'Bearer {token}'},
            data={'channel': channel, 'text': text},
            timeout=15
        )
        r_msg.raise_for_status()
        response_data = r_msg.json()
        ok = bool(response_data.get('ok'))
        if ok:
            print(f"[SLACK_NOTIFY] [OK] Mensagem enviada com sucesso para channel: {channel}")
        else:
            error = response_data.get('error', 'Unknown error')
            print(f"[SLACK_NOTIFY] [ERRO] Erro do Slack API: {error}")
            print(f"[SLACK_NOTIFY] Resposta completa: {response_data}")
            
            # Se o erro for channel_not_found e o ID começar com 'D', tenta buscar o user ID
            if error == 'channel_not_found' and sid.startswith('D'):
                print(f"[SLACK_NOTIFY] Tentando buscar user ID para o DM channel...")
                # Tenta usar o email do vendedor se disponível
                # (isso seria passado como fallback, mas por enquanto vamos apenas logar)
                print(f"[SLACK_NOTIFY] [AVISO] DM channel não encontrado. Verifique se o bot tem acesso ao canal ou use o user ID (U...) ao invés do DM ID (D...)")
        return ok
    except Exception as e:
        print(f"[SLACK_NOTIFY] [ERRO] Exceção ao enviar mensagem: {e}")
        import traceback
        traceback.print_exc()
        return False
