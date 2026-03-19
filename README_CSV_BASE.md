# Sistema CSV Base - Outbound Automático

## 📋 Visão Geral

Este sistema permite adicionar contatos em um CSV base e enviar mensagens automaticamente via WhatsApp. A Íris fica ativa e processa novos contatos automaticamente.

## 📁 Arquivos

- **`outbound_data/contatos_base.csv`** - CSV base onde você adiciona novos contatos
- **`scripts/monitor_contatos_base.py`** - Monitora o CSV e detecta novos contatos
- **`whatsapp-bot/send_from_queue.js`** - Envia mensagens para contatos pendentes
- **`scripts/auto_send_from_base.py`** - Sistema completo automático

## 🚀 Como Usar

### 1. Adicione Contatos no CSV Base

Abra o arquivo `outbound_data/contatos_base.csv` e adicione novos contatos:

```csv
telefone,empresa,contato,cargo,cidade,estado,email,cnpj,enviado,data_envio
5511999999999,Empresa Exemplo,Contato Exemplo,,,,,Não,
5511988887777,Outra Empresa,João Silva,Gerente,Curitiba,PR,joao@empresa.com,12345678000190,Não,
```

**Campos:**
- `telefone` - Telefone com DDD (ex: 5511988887777)
- `empresa` - Nome da empresa
- `contato` - Nome do contato
- `cargo` - Cargo (opcional)
- `cidade` - Cidade (opcional)
- `estado` - Estado/UF (opcional)
- `email` - Email (opcional)
- `cnpj` - CNPJ (opcional)
- `enviado` - Deixe como "Não" ou vazio para novos contatos
- `data_envio` - Será preenchido automaticamente

### 2. Execute o Sistema Automático

```bash
python scripts/auto_send_from_base.py
```

Este script:
- Monitora o CSV base continuamente
- Detecta novos contatos (onde `enviado` = "Não" ou vazio)
- Cria registro no sistema de follow-ups
- Envia mensagem via WhatsApp
- Marca como enviado no CSV base
- Atualiza o CSV de tracking

### 3. Ou Use os Scripts Separadamente

**Monitor apenas (detecta e registra):**
```bash
python scripts/monitor_contatos_base.py
```

**Enviar mensagens da fila:**
```bash
node whatsapp-bot/send_from_queue.js
```

## 🔄 Fluxo Automático

1. **Você adiciona contato no CSV base** com `enviado = "Não"`
2. **Monitor detecta** o novo contato (verifica a cada 10 segundos)
3. **Sistema cria registro** em `follow_ups.json`
4. **Mensagem é enviada** via WhatsApp
5. **CSV base é atualizado** marcando como enviado
6. **CSV de tracking é atualizado** com todos os dados
7. **Follow-ups são agendados** automaticamente (2 dias e 9 dias)

## 📊 CSV de Tracking

Todos os contatos processados aparecem automaticamente em:
`outbound_data/outbound_tracking.csv`

Com todos os dados:
- Status de resposta
- Agendamento
- Nível de interesse
- Follow-ups
- Dados técnicos coletados

## ⚙️ Requisitos

- Bot WhatsApp conectado (`node whatsapp-bot/index.js`)
- Serviço AI rodando (`python -m uvicorn main:app --port 8001`)
- Node.js instalado

## 💡 Dicas

- **Mantenha o CSV base aberto** para adicionar contatos facilmente
- **O monitor roda continuamente** - não precisa reiniciar ao adicionar contatos
- **Contatos já enviados** são ignorados automaticamente
- **Formato do telefone:** Apenas dígitos, com DDD (ex: 5511988887777)

## 🔍 Verificar Status

Para ver quais contatos foram processados:
- Abra `outbound_data/contatos_base.csv` - coluna `enviado` mostra "Sim"
- Abra `outbound_data/follow_ups.json` - lista todos os contatos no sistema
- Abra `outbound_data/outbound_tracking.csv` - CSV completo com todos os dados

## 🕵️ Scrape rápido de leads

O script `python scripts/scrape_bing_contacts.py`:
1. Pesquisa cada razão social (apenas a razão social) no Bing e coleta os **5 primeiros sites** retornados.
2. Para cada site visita também links destacados (menu, rodapé, redes sociais, “linktree”/Canva etc.) até uma profundidade de 2 saltos, mantendo um máximo de 12 links por página.
3. Em cada página visitada busca:
   - URLs do tipo `api.whatsapp.com/send?phone=` ou `wa.me/`;
   - Telefones próximos de palavras-chave (“WhatsApp”, “fone”, “contato” etc.);
   - Padrões `55 + DDD (2 ou 3 dígitos) + 8/9 dígitos`, com o “9” sendo opcional.
4. Registra o primeiro número válido encontrado, a URL origem, as notas do método usado e a trilha completa (`scrape_path`) que levou até ali.
5. Executa o processo nos 15 primeiros leads (padrão), gera `leads_por_cnae_e_nome_scraped.csv` com os campos originais mais `telefone_scraped`, `scrape_url`, `scrape_notes` e `scrape_path`, e exige as dependências de `scripts/requirements_scraping.txt` (`requests`, `beautifulsoup4`, `playwright` + `playwright install chromium`).
