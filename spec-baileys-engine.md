# Spec: Motor de conversa Baileys com fallback para whatsapp-web.js

## 1. Objetivo
- Mudar o motor principal de conversa de `whatsapp-web.js` para o `@adiwajshing/baileys`, mantendo todas as regras atuais de allowlist, outbound, integração com o serviço de IA e buffer de mensagens.
- Garantir que o `whatsapp-web.js` continue disponível como fallback quando o Baileys não conseguir se manter conectado ou inicializar.
- Reutilizar o máximo possível da lógica presente em `whatsapp-bot/index.js`, só que desacoplando a dependência direta do cliente para que ele funcione independentemente do motor escolhido.

## 2. Contexto atual
- O `index.js` do `whatsapp-bot` já contém:
  - Configurações compartilhadas via `.env`/`config.json`.
  - Controle de allowlist/outbound e persistência de histórico/pendências.
  - Rotinas de envio humano, verificação de typing, buffering e envio de respostas da IA.
  - Geração de QR Code, reconexões automáticas e patchs pontuais no Puppeteer.
- Todas essas funcionalidades hoje invocam APIs específicas do `whatsapp-web.js`.

## 3. Requisitos principais
### 3.1 Abstração do provedor WhatsApp
- Criar um adaptador que represente eventos básicos (`ready`, `qr`, `message`, `typing`, `auth`, `disconnect`, `error`) e operações (`sendMessage`, `downloadMedia`, `destroy`, `sendPresence`) usadas por `index.js`.
- Esse adaptador deve permitir trocar a implementação em tempo de execução, sendo possível mudar do Baileys para o whatsapp-web.js sem alterar a lógica de negócio.

### 3.2 Implementação Baileys (motor padrão)
- Usar `@adiwajshing/baileys` com `makeWASocket` + `useSingleFileAuthState` (ou similar) para persistir sessão em algo como `whatsapp-bot/.baileys_auth`.
- Emitir eventos equivalentes aos usados em `index.js`, incluindo a captura do QR Code, o sinal de ready/conexão, e mensagens/informações de typing/edição se disponível.
- Implementar métodos auxiliares para compatibilizar com `sendHumanMessage` (por exemplo, temos o `sendPresenceUpdate('composing')` antes de enviar texto).
- Garantir que downloads de mídia (`msg.downloadMedia()` no wweb) funcionem com o Baileys (usar `downloadMediaMessage`).

### 3.3 Fallback para whatsapp-web.js
- Ao iniciar o bot, tentar conectar com o Baileys primeiro. Se houver erro fatal (falha de autenticação, QR não atualizado, conexão fechando repetidamente), iniciar o cliente de `whatsapp-web.js` com `LocalAuth` em paralelo ou em substituição.
- O fallback deve ser automático, com logs claros (`"[FALLBACK] Baileys caiu, inicializando whatsapp-web.js"`). Se `whatsapp-web.js` também falhar, manter a tendência de reintentar para que o bot continue rodando.
- O estado da sessão (`.wwebjs_auth`) deve continuar sendo usado pelo fallback exatamente como hoje; idealmente as duas bibliotecas não disputam o mesmo diretório.

### 3.4 Experiência e estabilidade
- A lógica de buffer, agendamento e envios de IA continua inalterada, agora delegando ao adaptador.
- O bot continua expondo endpoints de arquivos (`whatsapp_ready.json`, follow-ups etc.).
- Documentar o novo fluxo e dependências em README ou novos arquivos, deixando claro como reverter para o `whatsapp-web.js` caso necessário ou como forçar o fallback.

## 4. Critérios de aceite e testes
- O `node whatsapp-bot/index.js` deve levantar o Baileys, mostrar QR Code no console e permitir responder mensagens da allowlist/outbound exatamente como antes.
- Se o Baileys cair na fase de conexão, o sistema entra no modo whatsapp-web.js sem bloquear.
- Audio, texto e mensagens longas devem ser tratadas da mesma forma (sem regressão no buffering/typing).
- Atualizar `package.json` com `@adiwajshing/baileys` e qualquer utilitário necessário.
- Testes manuais sugeridos: 1) conectar com Baileys (escaneando QR), enviar mensagem e confirmar batimento; 2) forçar erro no Baileys (ex: apagar sessão) e verificar fallback; 3) verificar outbound pendente e auto greeting ainda funcionam.

## 5. Perguntas em aberto
1. Quando considerar que o Baileys “não dá conta”? Devemos cair para o `whatsapp-web.js` após a primeira falha de conexão (ex: `connection.update` com `close`) ou após X tentativas/tempo?
2. Precisamos de um meio manual de escolher o motor ativo (ex: variável `WHATSAPP_ENGINE=baileys|wweb`), ou o fallback deve ser totalmente automático?
3. As rotinas auxiliares (`scripts/enviar_mensagens_pendentes.js`, `wpp_js`) precisam usar o mesmo adaptador/engine ou continuam rodando com `whatsapp-web.js`? Ou devemos atualizar esses scripts também?
4. Há algum caso especial de compatibilidade com arquivos compartilhados de sessão que devo saber (por exemplo, o Baileys precisa separar `.baileys_auth` para cada QR)? Qual caminho de persistência preferem?

> Assim que essas perguntas estiverem respondidas, posso criar o `plan.md` com tarefas pequenas e seguir para a implementação.
