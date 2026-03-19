# Slack Bot Outbound

## Como rodar

O `main.py` agora inicia:
- AI Service (`ai-service`)
- WhatsApp Bot (`whatsapp-bot/index.js`)
- Slack Bot (`slack-bot/main.py`)

Comando:

```bash
python main.py
```

Para desativar o Slack Bot:

```bash
python main.py --no-slack-bot
```

## Variaveis necessarias

No `.env`:
- `SLACK_BOT_TOKEN`
- `SLACK_APP_TOKEN`
- `SLACK_SIGNING_SECRET`
- `SLACK_AUTHORIZED_USER_ID` (opcional, override)

## Escopos minimos no app Slack

- `chat:write`
- `im:history`
- `im:write`
- `files:write`
- `commands`

Socket Mode deve estar habilitado.

## Comandos em DM

- `iniciar` (abre menu guiado)
- `status`
- `qr`
- `reset`
- `parar`
- `retomar`

## Arquivos de estado

- `outbound_data/slack_dispatch_state.json`
- `outbound_data/message_variants_log.json`
- `whatsapp_ready.json`
- `whatsapp_qr.json`
- `whatsapp_reset_request.json` (gerado sob demanda)
