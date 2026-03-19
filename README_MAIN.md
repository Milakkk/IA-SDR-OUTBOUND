# Script Principal - Sistema Íris Completo

## 🚀 Como Usar

### Opção 1: Python (Recomendado)
```bash
python main.py
```

### Opção 2: Windows Batch
```bash
main.bat
```

## 📋 O que o script faz

O script `main.py` inicia automaticamente:

1. **Serviço AI (FastAPI)**
   - Porta: 8001
   - URL: http://127.0.0.1:8001
   - Processa mensagens e gerencia conversas

2. **Bot WhatsApp**
   - Conecta ao WhatsApp Web
   - Recebe e envia mensagens
   - Integra com o serviço AI

3. **Monitor de Contatos Base** (Opcional)
   - Monitora `outbound_data/contatos_base.csv`
   - Detecta novos contatos automaticamente

4. **Atualizador de CSV** (Opcional)
   - Atualiza `outbound_data/outbound_tracking.csv` a cada 30 segundos
   - Mantém dados sempre atualizados

## ⚙️ Funcionalidades

- **Inicialização automática** de todos os serviços
- **Verificação de processos** - reinicia se parar
- **Encerramento seguro** - Ctrl+C encerra tudo
- **Interface interativa** - pergunta se quer iniciar serviços opcionais

## 📝 Fluxo de Uso

1. Execute `python main.py` (ou `python main.py --no-monitor` para rodar apenas a Iris).
2. O script levanta AI, bot e o monitor CSV; use `--no-monitor` para pular só o monitor.
3. Escaneie o QR Code quando aparecer
4. Sistema está pronto!

### Iniciar apenas a Iris
Caso queira levantar só o núcleo da Iris (AI + WhatsApp) sem o monitor CSV, execute:
```bash
python main.py --no-monitor
```


### Forcar novo QR Code do WhatsApp
Se o QR nao aparecer, limpe as sessoes e force nova autenticacao:
```bash
python main.py --reset-whatsapp-session
```
## 🔧 Requisitos

- Python 3.x
- Node.js instalado
- Dependências Python instaladas (`pip install -r ai-service/requirements.txt`)
- Dependências Node.js instaladas (`npm install` na pasta `whatsapp-bot`)

## 📊 Após Iniciar

### Adicionar Contatos
1. Edite `outbound_data/contatos_base.csv`
2. Adicione novos contatos com `enviado = Não`
3. Execute: `python scripts/processar_contatos_base.py`
4. Execute: `node whatsapp-bot/enviar_mensagens_pendentes.js`

### Verificar Status
- Serviço AI: http://127.0.0.1:8001/docs
- CSV Tracking: `outbound_data/outbound_tracking.csv`
- Follow-ups: `outbound_data/follow_ups.json`

## ⚠️ Observações

- O bot precisa escanear QR Code na primeira vez
- Se a porta 8001 estiver em uso, o script avisa
- Todos os processos são encerrados com Ctrl+C
- Logs aparecem no terminal


## Slack Bot
- python main.py agora tambem inicia o Slack Bot (slack-bot/main.py).
- python main.py --no-slack-bot para nao iniciar o Slack Bot.
- python main.py --authorized-slack-user UXXXXXXXX para override do usuario autorizado.
