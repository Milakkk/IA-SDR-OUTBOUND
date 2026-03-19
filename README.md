# IA SDR Outbound

Sistema de automação outbound para prospecção via WhatsApp com orquestração por IA, integração com CRM, apoio operacional por Slack e scripts de processamento de base.

Esta versão foi preparada para portfólio e análise técnica externa.

## Resumo

O projeto combina um serviço de IA em FastAPI com um bot de WhatsApp e integrações auxiliares para:

- iniciar contato outbound;
- interpretar respostas do lead;
- extrair dados relevantes da conversa;
- rotear atendimento;
- registrar contexto comercial;
- apoiar operação com Slack e CRM.

## Arquitetura

### 1. Orquestrador principal

`main.py` sobe os serviços centrais e reinicia processos caso parem:

- AI Service
- WhatsApp Bot
- monitor opcional de base CSV
- Slack Bot opcional

### 2. Serviço de IA

`ai-service/` concentra a lógica de negócio:

- processamento de mensagens;
- roteamento por setor;
- extração de dados do lead;
- integração com agenda;
- integração com CRM;
- persistência de contexto conversacional.

### 3. Bot de WhatsApp

`whatsapp-bot/` faz a ponte operacional com o canal:

- conexão com WhatsApp Web;
- envio de mensagens;
- recebimento de respostas;
- controle de estado;
- fallback entre engines.

### 4. Operação e automação

`slack-bot/` e `scripts/` cobrem tarefas auxiliares:

- acompanhamento operacional;
- disparos controlados;
- processamento de bases;
- atualização de tracking;
- rotinas de suporte ao fluxo outbound.

## Stack

- Python
- FastAPI
- Node.js
- WhatsApp Web.js / Baileys
- Slack Bolt
- OpenAI / DeepSeek
- Salesforce / Pipedrive / Google Calendar

## Pontos técnicos relevantes

- Orquestração multi-serviço com reinício automático.
- Separação entre canal, lógica conversacional e integrações.
- Configuração híbrida por `.env` e arquivos JSON.
- Estrutura preparada para operação outbound com tracking e follow-up.
- Código organizado por domínio, com módulos específicos para CRM, agenda, extração e persistência.

## Estrutura do repositório

- `ai-service/`: núcleo da IA e regras de negócio
- `whatsapp-bot/`: automação do canal WhatsApp
- `slack-bot/`: apoio operacional via Slack
- `scripts/`: utilitários e fluxos auxiliares
- `config.json`: configuração principal de execução
- `.env.example`: exemplo seguro de variáveis de ambiente

## Setup rápido

1. Crie um `.env` a partir de `.env.example`.
2. Ajuste `config.json` e os JSONs em `ai-service/` com seus próprios identificadores.
3. Instale dependências Python:

```bash
pip install -r ai-service/requirements.txt
pip install -r slack-bot/requirements.txt
```

4. Instale dependências Node.js:

```bash
cd whatsapp-bot && npm install
```

5. Inicie o sistema:

```bash
python main.py
```

## Publicação sanitizada

Para tornar este repositório publicável, foram removidos:

- credenciais e tokens;
- backups de ambiente;
- conversas reais e arquivos de mídia;
- bases CSV e artefatos comerciais;
- logs e estados operacionais;
- arquivos de teste e conteúdo interno não apropriado para exposição.

Os valores restantes de configuração são placeholders.

## Observação

Este repositório mostra a arquitetura e a implementação do sistema. Parte dos fluxos originais depende de dados e credenciais privadas e, por isso, não está incluída nesta versão pública.
