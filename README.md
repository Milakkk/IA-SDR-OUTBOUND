# IA SDR Outbound

Projeto de automação outbound com WhatsApp, FastAPI, Slack e integrações de CRM, preparado em versão pública para portfólio e revisão técnica.

## O que está neste repositório

- Serviço de IA em `ai-service/` para roteamento, extração e orquestração.
- Bot de WhatsApp em `whatsapp-bot/`.
- Bot operacional no Slack em `slack-bot/`.
- Scripts auxiliares de processamento e automação em `scripts/`.

## O que foi removido da versão pública

- Credenciais, tokens, backups de `.env` e IDs operacionais.
- Conversas reais, bases CSV, PDFs comerciais, logs e estados de execução.
- Arquivos de teste e artefatos locais.

## Setup local

1. Copie `.env.example` para `.env` e preencha as variáveis necessárias.
2. Revise `config.json` e os arquivos `ai-service/*.json` com seus próprios IDs.
3. Instale dependências Python e Node.js.
4. Inicie o projeto com `python main.py` ou execute serviços isolados conforme a documentação existente.

## Observações

- Os valores de configuração nesta versão são placeholders.
- O repositório foi sanitizado para publicação externa; fluxos que dependem de dados privados não estão incluídos.
