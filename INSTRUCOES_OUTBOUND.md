# Instruções - Sistema Outbound com Tracking CSV

## 📋 Visão Geral

O sistema está configurado para:
- Enviar mensagens outbound para prospects
- Rastrear todas as interações em CSV
- Detectar respostas automaticamente
- Gerenciar follow-ups
- Atualizar CSV periodicamente com todos os dados

## 🚀 Como Iniciar o Sistema

### Opção 1: Script Completo (Recomendado)

Execute em um único terminal:
```bash
python scripts/start_outbound_complete.py
```

Este script:
- Inicia o serviço AI
- Inicia o bot WhatsApp
- Prepara follow-ups
- Atualiza CSV automaticamente

### Opção 2: Manual (4 Terminais)

**Terminal 1 - Serviço AI:**
```bash
cd ai-service
python -m uvicorn main:app --host 127.0.0.1 --port 8001
```

**Terminal 2 - Bot WhatsApp:**
```bash
node whatsapp-bot/index.js
```
⚠️ Escaneie o QR Code quando aparecer!

**Terminal 3 - Enviar Mensagem (após conectar):**
```bash
node whatsapp-bot/send_test_outbound.js
```

**Terminal 4 - Atualizar CSV (opcional, mas recomendado):**
```bash
python scripts/run_outbound_with_csv.py
```

## 📤 Enviar Mensagem para +55 41 9997-2966

Após o bot WhatsApp estar conectado, execute:
```bash
node whatsapp-bot/send_test_outbound.js
```

Este script:
- Envia mensagem inicial para o número
- Cria registro em `outbound_data/follow_ups.json`
- Agenda follow-ups (2 dias e 9 dias)

## 📊 CSV de Tracking

### Localização
`outbound_data/outbound_tracking.csv`

### Campos Incluídos

**Dados Básicos:**
- telefone
- empresa
- contato
- cargo
- cidade
- estado
- email
- cnpj

**Status de Comunicação:**
- mensagem_inicial_enviada
- data_mensagem_inicial
- respondeu (Sim/Não)
- data_resposta

**Agendamento:**
- agendou_reuniao (Sim/Não)
- data_agendamento
- link_reuniao

**Interesse e Follow-ups:**
- nivel_interesse (Sem resposta / Baixo / Médio / Alto)
- necessita_followup (Sim/Não)
- follow_up_1_enviado
- data_follow_up_1
- follow_up_2_enviado
- data_follow_up_2

**Status e CRM:**
- status (active / responded / lost)
- lead_salesforce_id

**Dados Técnicos (Grow):**
- cultivo
- estrutura
- area

**Outros:**
- observacoes (JSON com todos os dados coletados)
- ultima_atualizacao

### Atualizar CSV Manualmente

```bash
python scripts/track_outbound_to_csv.py
```

### Atualização Automática

O script `run_outbound_with_csv.py` atualiza o CSV a cada 30 segundos automaticamente.

## 🔄 Fluxo Completo

1. **Mensagem Inicial:** Enviada via `send_test_outbound.js`
2. **Detecção de Resposta:** O bot (`index.js`) detecta automaticamente quando o cliente responde
3. **Atualização de Status:** O sistema atualiza `follow_ups.json` marcando como "responded"
4. **CSV Atualizado:** O CSV é atualizado com todos os dados coletados
5. **Follow-ups:** Se não responder, follow-ups são enviados automaticamente (via `check_follow_ups.js`)
6. **Lead Perdido:** Se não responder após todos os follow-ups, pode ser criado como "Lost" no Salesforce

## 📁 Arquivos Importantes

- `outbound_data/follow_ups.json` - Controle de follow-ups e status
- `outbound_data/outbound_tracking.csv` - CSV completo com todos os dados
- `conversations_state.json` - Estado das conversas (dados coletados, agendamentos)
- `whatsapp-bot/index.js` - Bot principal que detecta respostas
- `scripts/track_outbound_to_csv.py` - Gera CSV completo
- `scripts/run_outbound_with_csv.py` - Atualiza CSV periodicamente

## 🎯 Níveis de Interesse no CSV

- **Sem resposta:** Cliente não respondeu ainda
- **Baixo interesse (apenas respondeu):** Respondeu mas não qualificou
- **Médio interesse (qualificado):** Respondeu e forneceu dados, mas não agendou
- **Alto interesse (agendou):** Agendou reunião

## ⚠️ Observações

- O CSV é atualizado automaticamente quando você usa `run_outbound_with_csv.py`
- O sistema detecta respostas automaticamente através do `index.js`
- Follow-ups são gerenciados via `check_follow_ups.js` (executar periodicamente)
- Todos os dados coletados pela Íris aparecem no CSV
