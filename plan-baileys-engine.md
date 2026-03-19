# Plano: Engine Baileys com fallback automático

Este plano segue o `spec-baileys-engine.md` aprovado. O Baileys será o motor principal, mas o whatsapp-web.js permanece como fallback automático se o Baileys ficar 4 minutos tentando responder ou enfrentar erro, aproveitando o mesmo adaptador e persistindo as duas sessões ainda no início.

## Tarefas

1. [ ] **Definir adaptador WhatsApp genérico**  
   * Arquivos: `whatsapp-bot/index.js`, `whatsapp-bot/engines/whatsapp-engine.js` (novo)  
   * Mudança: criar interface comum (eventos: `qr`, `ready`, `message`, `typing`, etc; métodos: `sendMessage`, `downloadMedia`, `destroy`, `sendPresence`). Extrair da lógica atual tudo que depende diretamente do `client`.  
   * Verificação: `index.js` deve importar o adaptador, reagir aos eventos usando a mesma lógica de buffer/IA e compilar sem erros.

2. [ ] **Implementar engine Baileys com persistência**  
   * Arquivos: `whatsapp-bot/engines/baileys-engine.js` (novo), `.baileys_auth/`  
   * Mudança: usar `@adiwajshing/baileys` com `useSingleFileAuthState`, emitir eventos compatíveis, reproduzir download de mídia e suporte a presença/typing. Persistir sessão ao primeiro login e manter ambas as autenticações (`.baileys_auth` e `.wwebjs_auth`).  
   * Verificação: `node whatsapp-bot/index.js` exibe QR, entra em ready e responde mensagens da allowlist/outbound via Baileys sem precisar fallback.

3. [ ] **Implementar fallback automático para whatsapp-web.js**  
   * Arquivos: `whatsapp-bot/engines/wwebjs-engine.js` (novo/adaptado), `whatsapp-bot/index.js`  
   * Mudança: ao iniciar, criar instância Baileys; se o evento `connection.update` indicar desconexão e o bot ficar 4 minutos tentando responder ou se houver erro grave, iniciar o wweb.js e manter o loop de reconexão. Registrar logs `"[FALLBACK]"`.  
   * Verificação: simular falha forçando desconexão do Baileys e confirmar que o whatsapp-web.js assume automaticamente, continuando a lógica.

4. [ ] **Adequar lógica de reconexão, eventos e históricos ao adaptador**  
   * Arquivos: `whatsapp-bot/index.js`, `whatsapp-bot/outbound_data` (mesmo)  
   * Mudança: remover referências diretas a `client`, usar adaptador para `sendHumanMessage`, `sendPendingOutboundMessages`, etc. Garantir que fallback e Baileys compartilhem a mesma lógica de mensagens pendentes, allowlist e buffer.  
   * Verificação: fluxos de inbound/outbound/greeting funcionam seja no Baileys, seja no fallback.

5. [ ] **Atualizar dependências, scripts e documentação**  
   * Arquivos: `whatsapp-bot/package.json`, `README_MAIN.md`, `scripts/` (se necessário)  
   * Mudança: adicionar `@adiwajshing/baileys`, documentar novo comportamento (como forçar engine, onde ficam as sessões, critérios para fallback). Garantir que `npm install` instale tudo.  
   * Verificação: `npm install` completa, README descreve o fluxo e testes manuais (QR Baileys, fallback, outbound/outbound).


### Observação sobre testes manuais  
- 1) Iniciar bot, escanear QR no Baileys e validar respostas.  
- 2) Forçar desconexão após 4 minutos ou erro, e confirmar que `whatsapp-web.js` assume.  
- 3) Validar envio de outbound pendente e auto greeting em ambos os motores.
