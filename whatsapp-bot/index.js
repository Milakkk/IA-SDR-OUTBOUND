console.log('STARTING BOT...');
const path = require('path');
const fs = require('fs');
const dotenv = require('dotenv');

// SEMPRE usa o .env da raiz do projeto (unificado)
const rootEnvPath = path.resolve(__dirname, '..', '.env');
if (fs.existsSync(rootEnvPath)) {
  dotenv.config({ path: rootEnvPath });
  console.log('[ENV] Carregando .env da raiz:', rootEnvPath);
} else {
  // Fallback: tenta .env local, depois padrÃ£o
  const localEnvPath = path.resolve(process.cwd(), '.env');
  if (fs.existsSync(localEnvPath)) {
    dotenv.config({ path: localEnvPath });
    console.log('[ENV] Carregando .env local:', localEnvPath);
  } else {
    dotenv.config();
    console.log('[ENV] Usando .env padrÃ£o');
  }
}

const axios = require('axios');
const qrcode = require('qrcode-terminal');
const { createWwebJsEngine } = require('./engines/wwebjs-engine');
const { createBaileysEngine } = require('./engines/baileys-engine');

// Carrega config.json unificado (prioridade sobre .env)
const configPath = path.resolve(__dirname, '..', 'config.json');
let CONFIG = {};
if (fs.existsSync(configPath)) {
  try {
    CONFIG = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    console.log('[CONFIG] âœ… Carregando config.json unificado');
  } catch (e) {
    console.log('[CONFIG] âš ï¸ Erro ao carregar config.json:', e.message);
  }
}

const AI_SERVICE_URL = CONFIG.ai_service?.url || process.env.AI_SERVICE_URL || 'http://127.0.0.1:8001';
const HEADLESS = String(process.env.HEADLESS ?? '').toLowerCase() !== 'false';
const RESET_SESSION = ['1', 'true', 'yes', 'y', 'sim', 's'].includes(String(process.env.RESET_SESSION || '').trim().toLowerCase());
// Prioriza config.json, depois .env
const ALLOWED_CONTACTS_RAW = CONFIG.allowed_contacts || process.env.ALLOWED_CONTACTS || '';
const ALLOWED_CONTACTS = Array.isArray(ALLOWED_CONTACTS_RAW)
  ? ALLOWED_CONTACTS_RAW.map(n => String(n).replace(/\D/g, '')).filter(Boolean)
  : String(ALLOWED_CONTACTS_RAW)
    .split(',')
    .map(s => s.trim())
    .map(s => s.replace(/\D/g, ''))
    .filter(Boolean);
console.log('[INIT] ALLOWED_CONTACTS carregados:', ALLOWED_CONTACTS);
console.log('[INIT] Total de nÃºmeros na allowlist:', ALLOWED_CONTACTS.length);
console.log('[INIT] Fonte:', CONFIG.allowed_contacts ? 'config.json' : '.env');
const AUTO_GREETING_ON_READY = CONFIG.whatsapp?.auto_greeting_on_ready !== undefined
  ? CONFIG.whatsapp.auto_greeting_on_ready
  : String(process.env.AUTO_GREETING_ON_READY || 'false').toLowerCase() !== 'false';
const ENABLE_WWEBJS_FALLBACK = CONFIG.whatsapp?.enable_wwebjs_fallback !== undefined
  ? CONFIG.whatsapp.enable_wwebjs_fallback === true
  : String(process.env.ENABLE_WWEBJS_FALLBACK || 'false').toLowerCase() === 'true';
const greeted = new Set();
const PROJECT_ROOT = path.resolve(__dirname, '..');
const FOLLOW_UPS_FILE = path.resolve(PROJECT_ROOT, 'outbound_data', 'follow_ups.json');
const SENT_MESSAGES_FILE = path.resolve(PROJECT_ROOT, 'outbound_data', 'sent_messages.json');
const WHATSAPP_READY_FILE = path.resolve(PROJECT_ROOT, 'whatsapp_ready.json');
const WHATSAPP_QR_FILE = path.resolve(PROJECT_ROOT, 'whatsapp_qr.json');
const SLACK_DISPATCH_STATE_FILE = path.resolve(PROJECT_ROOT, 'outbound_data', 'slack_dispatch_state.json');
const MESSAGE_VARIANTS_LOG_FILE = path.resolve(PROJECT_ROOT, 'outbound_data', 'message_variants_log.json');
const WHATSAPP_RESET_REQUEST_FILE = path.resolve(PROJECT_ROOT, 'whatsapp_reset_request.json');
const INBOUND_QUEUE_FILE = path.resolve(PROJECT_ROOT, 'outbound_data', 'inbound_queue.json');
const INBOUND_AUDIT_FILE = path.resolve(PROJECT_ROOT, 'outbound_data', 'inbound_audit.jsonl');
const INBOUND_SESSIONS_FILE = path.resolve(PROJECT_ROOT, 'outbound_data', 'inbound_sessions.json');
const IRIS_ALERT_PHONE = onlyDigits(process.env.IRIS_ALERT_PHONE || '');

const SLACK_BOT_CFG = CONFIG.slack_bot || {};
const GUARDRAILS = SLACK_BOT_CFG.guardrails || {};
const BATCH_CAP = Number(GUARDRAILS.batch_cap || 80);
const HOURLY_CAP = Number(GUARDRAILS.hourly_cap || 120);
const DAILY_CAP = Number(GUARDRAILS.daily_cap || 5);
const DAILY_WINDOW_HOURS = Number(GUARDRAILS.daily_window_hours || 24);
const MIN_INTERVAL_SECONDS = Number(GUARDRAILS.min_interval_seconds || 6);
const MAX_INTERVAL_SECONDS = Number(GUARDRAILS.max_interval_seconds || 14);
const BREAK_EVERY = Number(GUARDRAILS.break_every || 20);
const BREAK_SECONDS = Number(GUARDRAILS.break_seconds || 90);
const MAX_CONSECUTIVE_ERRORS = Number(GUARDRAILS.max_consecutive_errors || 5);
const PAUSE_MINUTES_ON_CIRCUIT_BREAKER = Number(GUARDRAILS.pause_minutes_on_circuit_breaker || 10);
const QUIET_HOURS = SLACK_BOT_CFG.quiet_hours || { start: '08:00', end: '20:30' };
const DISPATCH_TIMEZONE = 'America/Sao_Paulo';

let outboundSendLock = false;
let outboundInterval = null;
let WPP_READY = false;
let ENGINE_READY = false;
let inboundQueueLock = false;

let activeEngine = null;
const FALLBACK_DELAY_MS = 4 * 60 * 1000;
const FALLBACK_ENGINE_ID = 'wwebjs';
let fallbackTimer = null;
let disconnectCount = 0;
const BAILEYS_405_WINDOW_MS = 2 * 60 * 1000;
const BAILEYS_405_MAX_RETRIES = 4;
const BAILEYS_405_COOLDOWN_MS = 60 * 1000;
let baileys405Timestamps = [];

function getActiveEngine() {
  if (!activeEngine) {
    throw new Error('Motor WhatsApp nÃ£o inicializado');
  }
  return activeEngine;
}

async function destroyActiveEngine() {
  if (!activeEngine) return;
  try {
    await activeEngine.destroy();
  } catch (e) {
    console.log('[ENGINE] Erro ao destruir engine:', String(e.message || e));
  }
  activeEngine = null;
  if (outboundInterval) {
    try { clearInterval(outboundInterval); } catch {}
    outboundInterval = null;
  }
  WPP_READY = false;
  ENGINE_READY = false;
  cancelFallbackTimer();
}

// ========== PERSISTÃŠNCIA DE CONVERSAS ==========
const CONVERSATIONS_FILE = path.resolve(process.cwd(), 'conversations.json');
let conversationHistory = {};

function loadConversations() {
  try {
    if (fs.existsSync(CONVERSATIONS_FILE)) {
      const data = fs.readFileSync(CONVERSATIONS_FILE, 'utf8');
      conversationHistory = JSON.parse(data);
      console.log('[PERSIST] Conversas carregadas:', Object.keys(conversationHistory).length, 'contatos');
    }
  } catch (e) {
    console.log('[PERSIST] Erro ao carregar conversas:', e.message);
    conversationHistory = {};
  }
}

function saveConversations() {
  try {
    const tempFile = `${CONVERSATIONS_FILE}.tmp`;
    fs.writeFileSync(tempFile, JSON.stringify(conversationHistory, null, 2), 'utf8');
    fs.renameSync(tempFile, CONVERSATIONS_FILE);
  } catch (e) {
    console.log('[PERSIST] Erro ao salvar conversas:', e.message);
  }
}

function addToHistory(contactId, role, message) {
  if (!conversationHistory[contactId]) {
    conversationHistory[contactId] = { messages: [], lastUpdate: Date.now(), pendingMessage: null };
  }
  conversationHistory[contactId].messages.push({ role, message, timestamp: Date.now() });
  conversationHistory[contactId].lastUpdate = Date.now();
  // Limita histÃ³rico a Ãºltimas 20 mensagens por contato
  if (conversationHistory[contactId].messages.length > 20) {
    conversationHistory[contactId].messages = conversationHistory[contactId].messages.slice(-20);
  }
  saveConversations();
}

function setPendingMessage(contactId, message) {
  if (!conversationHistory[contactId]) {
    conversationHistory[contactId] = { messages: [], lastUpdate: Date.now(), pendingMessage: null };
  }
  conversationHistory[contactId].pendingMessage = message;
  saveConversations();
}

function clearPendingMessage(contactId) {
  if (conversationHistory[contactId]) {
    conversationHistory[contactId].pendingMessage = null;
    saveConversations();
  }
}

function getPendingMessage(contactId) {
  return conversationHistory[contactId]?.pendingMessage || null;
}

loadConversations();
// ===============================================

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

function isMarkedUnreadError(err) {
  return String((err && err.message) || err || '').includes('markedUnread');
}

async function sendHumanMessage(id, text, options = {}) {
  const len = String(text || '').length;
  const base = 500;
  const perChar = 60; // ~16 chars/sec, fast but readable speed
  const max = 20000;
  const jitter = Math.floor(Math.random() * 500);
  const typingDelay = Math.min(base + len * perChar + jitter, max);
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const engine = getActiveEngine();
      if (typeof engine.isRegisteredUser === 'function') {
        const exists = await engine.isRegisteredUser(id);
        if (!exists) {
          throw new Error(`Numero sem WhatsApp ativo: ${id}`);
        }
      }
      const chat = await getActiveEngine().getChatById(id);
      // await chat.sendStateTyping();
      await delay(typingDelay);
      const result = await getActiveEngine().sendMessage(id, text);
      addToHistory(onlyDigits(id), 'assistant', text);
      rememberBotOutbound(id, text);
      if (!result) {
        throw new Error(`Envio sem confirmacao de retorno: ${id}`);
      }
      if (options.requireAck) {
        const messageId = String(result?.messageId || result?.raw?.key?.id || result?.key?.id || '').trim();
        if (!messageId) {
          if (options.allowMissingAck) {
            return { ...result, ackConfirmed: false, ackWarning: `Envio sem messageId: ${id}` };
          }
          throw new Error(`Envio sem messageId: ${id}`);
        }
        const ackOk = await getActiveEngine().waitForOutboundAck(
          { chatId: id, messageId },
          Number(options.ackTimeoutMs || 25000)
        );
        if (!ackOk) {
          if (options.allowMissingAck) {
            return { ...result, ackConfirmed: false, ackWarning: `Sem confirmacao de entrega: ${id}` };
          }
          throw new Error(`Sem confirmacao de entrega: ${id}`);
        }
        return { ...result, ackConfirmed: true };
      }
      // try { await chat.clearState(); } catch {}
      return result;
    } catch (e) {
      if (attempt === 1 && isMarkedUnreadError(e)) {
        console.log('[WWEBJS] Erro markedUnread detectado; reaplicando patch e tentando novamente...');
        try {
          await getActiveEngine().applyBrowserPatch();
        } catch {}
        await delay(800);
        continue;
      }
      if (attempt === 2) throw e;
    }
  }
}

function safeReadJson(filePath, fallback) {
  try {
    if (!fs.existsSync(filePath)) return fallback;
    const raw = fs.readFileSync(filePath, 'utf8');
    if (!raw || !raw.trim()) return fallback;
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function safeWriteJson(filePath, data) {
  try {
    const dir = path.dirname(filePath);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf8');
    return true;
  } catch {
    return false;
  }
}

function readDispatchState() {
  return safeReadJson(SLACK_DISPATCH_STATE_FILE, {
    paused: false,
    pause_until: null,
    pause_reason: '',
    active_batch_id: null,
    base_message: '',
    tone: SLACK_BOT_CFG.default_tone || 'aggressive',
    consecutive_errors: 0,
    batch_sent_count: 0,
    hourly_sent_timestamps: [],
    daily_sent_timestamps: [],
    sent_since_break: 0,
    last_updated_at: null
  });
}

function writeDispatchState(state) {
  state.last_updated_at = new Date().toISOString();
  return safeWriteJson(SLACK_DISPATCH_STATE_FILE, state);
}

function parseHm(value, fallback) {
  const raw = String(value || fallback || '').trim();
  const m = raw.match(/^(\d{1,2}):(\d{2})$/);
  if (!m) return fallback;
  const hh = Math.max(0, Math.min(23, Number(m[1])));
  const mm = Math.max(0, Math.min(59, Number(m[2])));
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
}

function isInsideQuietHours(date = new Date()) {
  const start = parseHm(QUIET_HOURS.start, '08:00');
  const end = parseHm(QUIET_HOURS.end, '20:30');
  const [sh, sm] = start.split(':').map(Number);
  const [eh, em] = end.split(':').map(Number);
  const nowMinutes = date.getHours() * 60 + date.getMinutes();
  const startMinutes = sh * 60 + sm;
  const endMinutes = eh * 60 + em;
  return nowMinutes >= startMinutes && nowMinutes <= endMinutes;
}

function isBusinessDay(date = new Date()) {
  const day = date.getDay();
  return day >= 1 && day <= 5;
}

function cleanupHourlyTimestamps(state) {
  const now = Date.now();
  const oneHour = 60 * 60 * 1000;
  const arr = Array.isArray(state.hourly_sent_timestamps) ? state.hourly_sent_timestamps : [];
  state.hourly_sent_timestamps = arr.filter(ts => Number(ts) > 0 && (now - Number(ts)) <= oneHour);
}

function cleanupDailyTimestamps(state) {
  const todayKey = new Intl.DateTimeFormat('en-CA', {
    timeZone: DISPATCH_TIMEZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  }).format(new Date());
  const arr = Array.isArray(state.daily_sent_timestamps) ? state.daily_sent_timestamps : [];
  state.daily_sent_timestamps = arr.filter((ts) => {
    const ms = Number(ts);
    if (!(ms > 0)) return false;
    const dayKey = new Intl.DateTimeFormat('en-CA', {
      timeZone: DISPATCH_TIMEZONE,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }).format(new Date(ms));
    return dayKey === todayKey;
  });
}

function stableHash(text) {
  let h = 2166136261;
  const s = String(text || '');
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h += (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24);
  }
  return (h >>> 0).toString(16);
}

function fallbackVariant(baseMessage, empresa, city, state) {
  const base = String(baseMessage || '').trim();
  const templates = [
    `Ola, tudo bem?\n\nSou o Eduardo, da Silicon, e trabalhamos com iluminacao para o aumento de fotossintese de mudas.\n\nVi que voces trabalham com producao de mudas e preciso falar com o responsavel pelo manejo do viveiro.\n\nConsegue me ajudar a falar com o responsavel?`,
    `Ola, tudo certo?\n\nSou o Eduardo, da Silicon, e atuamos com iluminacao para aumentar a fotossintese de mudas.\n\nVi que voces produzem mudas e preciso falar com quem responde pelo manejo do viveiro.\n\nVoce consegue me indicar o responsavel?`,
    `Ola, tudo bem?\n\nSou o Eduardo, da Silicon, e trabalhamos com iluminacao voltada ao aumento de fotossintese de mudas.\n\nPercebi que voces trabalham com producao de mudas e preciso falar com a pessoa responsavel pelo manejo do viveiro.\n\nPode me ajudar a chegar nessa pessoa?`
  ];
  if (!base) return templates[Math.floor(Math.random() * templates.length)];
  const v = [
    base.replace('Ola, tudo bem?', 'Ola, tudo certo?'),
    base.replace('preciso falar com o responsavel pelo manejo do viveiro.', 'queria falar com o responsavel pelo manejo do viveiro.'),
    base.replace('Consegue me ajudar a falar com o responsavel?', 'Voce consegue me indicar o responsavel?'),
    base.replace('Consegue me ajudar a falar com o responsavel?', 'Pode me ajudar a chegar nessa pessoa?')
  ].filter(Boolean);
  return v[Math.floor(Math.random() * v.length)] || templates[Math.floor(Math.random() * templates.length)];
}

async function generateOutboundVariant(baseMessage, entry, tone) {
  const variantsLog = safeReadJson(MESSAGE_VARIANTS_LOG_FILE, { hashes: {}, items: [] });
  const hashes = variantsLog.hashes || {};
  for (let attempt = 1; attempt <= 3; attempt++) {
    let text = '';
    try {
      const url = `${AI_SERVICE_URL.replace(/\/$/, '')}/outbound/variant`;
      const payload = {
        base_message: baseMessage || '',
        // Mantem variante sem personalizacao por empresa/cidade.
        empresa: '',
        city: '',
        state: '',
        tone: tone || (SLACK_BOT_CFG.default_tone || 'aggressive'),
        constraints: { max_chars: 700 }
      };
      const res = await axios.post(url, payload, { timeout: 30000 });
      text = String((res.data && res.data.variant_message) || '').trim();
    } catch {}

    if (!text) {
      text = fallbackVariant(baseMessage, entry.empresa, entry.cidade, entry.estado);
    }
    const hash = stableHash(text);
    if (hashes[hash]) {
      continue;
    }
    hashes[hash] = true;
    variantsLog.hashes = hashes;
    variantsLog.items = Array.isArray(variantsLog.items) ? variantsLog.items : [];
    variantsLog.items.push({
      hash,
      text,
      phone: entry.telefone || '',
      empresa: entry.empresa || '',
      at: new Date().toISOString()
    });
    if (variantsLog.items.length > 3000) {
      variantsLog.items = variantsLog.items.slice(-3000);
    }
    safeWriteJson(MESSAGE_VARIANTS_LOG_FILE, variantsLog);
    return text;
  }
  return fallbackVariant(baseMessage, entry.empresa, entry.cidade, entry.estado);
}

function setWhatsAppReady(ready, meta) {
  safeWriteJson(WHATSAPP_READY_FILE, {
    ready: Boolean(ready),
    at: new Date().toISOString(),
    ...(meta || {})
  });
  WPP_READY = Boolean(ready);
}

async function checkExternalResetRequest() {
  try {
    if (!fs.existsSync(WHATSAPP_RESET_REQUEST_FILE)) return false;
    const req = safeReadJson(WHATSAPP_RESET_REQUEST_FILE, {});
    try { fs.rmSync(WHATSAPP_RESET_REQUEST_FILE, { force: true }); } catch {}
    console.log('[RESET] SolicitaÃ§Ã£o externa detectada:', req);

    try {
      if (fs.existsSync(path.resolve(PROJECT_ROOT, '.wwebjs_auth'))) {
        fs.rmSync(path.resolve(PROJECT_ROOT, '.wwebjs_auth'), { recursive: true, force: true });
      }
    } catch {}
    try {
      if (fs.existsSync(path.resolve(PROJECT_ROOT, '.baileys_auth'))) {
        fs.rmSync(path.resolve(PROJECT_ROOT, '.baileys_auth'), { recursive: true, force: true });
      }
    } catch {}
    setWhatsAppReady(false, { event: 'external_reset', requested_by: req.requested_by || 'unknown' });
    safeWriteJson(WHATSAPP_QR_FILE, {
      qr: '',
      generated_at: new Date().toISOString(),
      event: 'reset_pending'
    });
    restartEngine(0, currentFactoryIndex);
    return true;
  } catch (e) {
    console.log('[RESET] Falha ao processar reset externo:', String(e && e.message || e));
    return false;
  }
}

function outboundDefaultMessage(empresa) {
  return `Ola, tudo bem?\n\nSou o Eduardo, da Silicon, e trabalhamos com iluminacao para o aumento de fotossintese de mudas.\n\nVi que voces trabalham com producao de mudas e preciso falar com o responsavel pelo manejo do viveiro.\n\nConsegue me ajudar a falar com o responsavel?`;
}

async function sendPendingOutboundMessages() {
  await checkExternalResetRequest();
  if (!WPP_READY || !ENGINE_READY) return;
  if (outboundSendLock) return;
  outboundSendLock = true;
  try {
    const state = readDispatchState();
    const now = new Date();

    if (state.paused) {
      const pauseUntil = state.pause_until ? new Date(state.pause_until) : null;
      if (pauseUntil && Date.now() >= pauseUntil.getTime()) {
        state.paused = false;
        state.pause_until = null;
        state.pause_reason = '';
        writeDispatchState(state);
      } else {
        return;
      }
    }

    if (!isBusinessDay(now)) return;
    if (!isInsideQuietHours(now)) return;

    cleanupHourlyTimestamps(state);
    cleanupDailyTimestamps(state);
    if (state.hourly_sent_timestamps.length >= HOURLY_CAP) {
      writeDispatchState(state);
      return;
    }
    if (state.daily_sent_timestamps.length >= DAILY_CAP) {
      writeDispatchState(state);
      return;
    }

    const followUps = safeReadJson(FOLLOW_UPS_FILE, {});
    const phones = Object.keys(followUps || {});
    if (!phones.length) return;

    let pendingPhones = phones.filter((p) => {
      const entry = followUps[p] || {};
      const sentAt = String(entry.sent_at || '').trim();
      const status = String(entry.status || 'active').toLowerCase();
      const isPending = status === 'active' && !sentAt;
      if (!isPending) return false;
      if (state.active_batch_id) {
        return String(entry.batch_id || '') === String(state.active_batch_id);
      }
      return true;
    }).sort((a, b) => {
      const entryA = followUps[a] || {};
      const entryB = followUps[b] || {};
      const priA = entryA.priority_redirect === true ? 1 : 0;
      const priB = entryB.priority_redirect === true ? 1 : 0;
      if (priA !== priB) return priB - priA;
      const tsA = String(entryA.priority_created_at || entryA.queued_at || '');
      const tsB = String(entryB.priority_created_at || entryB.queued_at || '');
      return tsA.localeCompare(tsB);
    });

    if (!pendingPhones.length && state.active_batch_id) {
      state.active_batch_id = null;
      state.last_updated_at = new Date().toISOString();
      writeDispatchState(state);
      pendingPhones = phones.filter((p) => {
        const entry = followUps[p] || {};
        const sentAt = String(entry.sent_at || '').trim();
        const status = String(entry.status || 'active').toLowerCase();
        return status === 'active' && !sentAt;
      }).sort((a, b) => {
        const entryA = followUps[a] || {};
        const entryB = followUps[b] || {};
        const priA = entryA.priority_redirect === true ? 1 : 0;
        const priB = entryB.priority_redirect === true ? 1 : 0;
        if (priA !== priB) return priB - priA;
        const tsA = String(entryA.priority_created_at || entryA.queued_at || '');
        const tsB = String(entryB.priority_created_at || entryB.queued_at || '');
        return tsA.localeCompare(tsB);
      });
    }

    if (!pendingPhones.length) return;
    console.log('[OUTBOUND] Mensagens pendentes encontradas:', pendingPhones.length);

    const sentMessages = safeReadJson(SENT_MESSAGES_FILE, {});
    let sentInThisCycle = 0;
    let consecutiveErrors = Number(state.consecutive_errors || 0);
    let batchSentCount = Number(state.batch_sent_count || 0);
    let sentSinceBreak = Number(state.sent_since_break || 0);
    const baseMessage = String(state.base_message || '').trim();
    const tone = String(state.tone || (SLACK_BOT_CFG.default_tone || 'aggressive'));

    for (const phoneDigits of pendingPhones) {
      cleanupHourlyTimestamps(state);
      cleanupDailyTimestamps(state);
      if (sentInThisCycle >= 5) break;
      if (batchSentCount >= BATCH_CAP) break;
      if (state.hourly_sent_timestamps.length >= HOURLY_CAP) break;
      if (state.daily_sent_timestamps.length >= DAILY_CAP) break;

      const entry = followUps[phoneDigits] || {};
      const empresa = entry.empresa || 'Empresa';
      const to = `${String(phoneDigits).replace(/\D/g, '')}@c.us`;
      const sourceMessage = String(entry.base_message || baseMessage || outboundDefaultMessage(empresa));
      const text = await generateOutboundVariant(
        sourceMessage,
        { ...entry, telefone: String(phoneDigits).replace(/\D/g, '') },
        tone
      );
      const nowIso = new Date().toISOString();
      const digitsOnly = String(phoneDigits).replace(/\D/g, '');

      try {
        console.log('[OUTBOUND] Enviando mensagem inicial para:', digitsOnly, '-', empresa);
        const sendResult = await sendHumanMessage(to, text, {
          requireAck: true,
          ackTimeoutMs: 25000,
          allowMissingAck: true
        });
        entry.sent_at = nowIso;
        entry.sent_message = text;
        entry.last_error = '';
        entry.ack_confirmed = sendResult?.ackConfirmed !== false;
        entry.ack_warning = sendResult?.ackWarning || '';
        entry.send_attempts = Number(entry.send_attempts || 0) + 1;
        followUps[phoneDigits] = entry;
        sentMessages[phoneDigits] = {
          status: entry.ack_confirmed ? 'success' : 'success_without_ack',
          sent_at: nowIso,
          ...(entry.ack_warning ? { warning: entry.ack_warning } : {})
        };
        outboundContacts.add(digitsOnly);
        irisInitiatedContacts.add(digitsOnly);
        sentInThisCycle += 1;
        consecutiveErrors = 0;
        batchSentCount += 1;
        sentSinceBreak += 1;
        state.hourly_sent_timestamps.push(Date.now());
        state.daily_sent_timestamps.push(Date.now());
        state.consecutive_errors = 0;
        state.batch_sent_count = batchSentCount;
        state.sent_since_break = sentSinceBreak;
        writeDispatchState(state);
        if (entry.ack_confirmed) {
          console.log('[OUTBOUND] ? Enviado para:', digitsOnly);
        } else {
          console.log('[OUTBOUND] ? Enviado sem ACK confirmado para:', digitsOnly, '-', entry.ack_warning);
        }

        safeWriteJson(FOLLOW_UPS_FILE, followUps);
        safeWriteJson(SENT_MESSAGES_FILE, sentMessages);

        if (sentSinceBreak >= BREAK_EVERY) {
          await delay(BREAK_SECONDS * 1000);
          sentSinceBreak = 0;
          state.sent_since_break = 0;
          writeDispatchState(state);
        } else {
          const spread = Math.max(0, MAX_INTERVAL_SECONDS - MIN_INTERVAL_SECONDS);
          const jitterSeconds = Math.floor(Math.random() * (spread + 1));
          await delay((MIN_INTERVAL_SECONDS + jitterSeconds) * 1000);
        }
      } catch (e) {
        const errorText = String(e && e.message || e);
        entry.send_attempts = Number(entry.send_attempts || 0) + 1;
        entry.last_error = errorText;
        const isInvalid = errorText.includes('Numero sem WhatsApp ativo');
        if (isInvalid) {
          entry.status = 'invalid_number';
          sentMessages[phoneDigits] = { status: 'invalid_number', sent_at: nowIso, error: errorText };
        } else if (entry.send_attempts >= 3) {
          entry.status = 'error';
          sentMessages[phoneDigits] = { status: 'error', sent_at: nowIso, error: errorText };
        } else {
          entry.status = 'active';
          sentMessages[phoneDigits] = { status: 'retry_pending', sent_at: nowIso, error: errorText, attempts: entry.send_attempts };
        }
        followUps[phoneDigits] = entry;
        consecutiveErrors += 1;
        state.consecutive_errors = consecutiveErrors;
        if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
          state.paused = true;
          state.pause_reason = 'circuit_breaker';
          state.pause_until = new Date(Date.now() + (PAUSE_MINUTES_ON_CIRCUIT_BREAKER * 60 * 1000)).toISOString();
          writeDispatchState(state);
          break;
        }
        writeDispatchState(state);
        console.log('[OUTBOUND] ? Falha ao enviar para:', digitsOnly, '-', errorText);
      }
    }

    safeWriteJson(FOLLOW_UPS_FILE, followUps);
    safeWriteJson(SENT_MESSAGES_FILE, sentMessages);
  } finally {
    outboundSendLock = false;
  }
}

async function sendGreetings(client) {
  if (!ALLOWED_CONTACTS.length) return;
  for (const digits of ALLOWED_CONTACTS) {
    const id = `${digits}@c.us`;
    if (greeted.has(id)) continue;
    greeted.add(id);
    try {
      await sendHumanMessage(id, 'Oi! Sou a Ãris, da Silicon. Tudo bem? Para te atender melhor, qual Ã© seu nome, por favor?');
      await delay(800);
      await sendHumanMessage(id, 'Somos a Silicon: usinas fotovoltaicas, grow, iluminaÃ§Ã£o esportiva e industrial, e geraÃ§Ã£o distribuÃ­da (GD), sempre com suporte tÃ©cnico prÃ³ximo.');
      await delay(800);
      await sendHumanMessage(id, 'Posso te ajudar com um orÃ§amento, informaÃ§Ãµes objetivas ou agendar uma conversa com um de nossos consultores. O que vocÃª prefere?');
    } catch { }
  }
}

function scheduleStartupGreetings(client) {
  if (!AUTO_GREETING_ON_READY || !ALLOWED_CONTACTS.length) return;
  let attempts = 0;
  const maxAttempts = 5;
  const backoff = () => Math.min(2000 + attempts * 1000, 8000);
  const run = async () => {
    attempts++;
    try {
      await sendGreetings(client);
    } catch { }
    if (attempts < maxAttempts) {
      setTimeout(run, backoff());
    }
  };
  setTimeout(run, backoff());
}

function onlyDigits(s) {
  return String(s || '').replace(/\D/g, '');
}

function extractEmails(text) {
  return Array.from(new Set(String(text || '').match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) || []));
}

function extractCandidatePhones(text) {
  const digits = onlyDigits(text);
  const found = [];
  for (let i = 0; i < digits.length; i++) {
    for (const len of [13, 12, 11, 10]) {
      const candidate = digits.slice(i, i + len);
      if (candidate.length < len) continue;
      if (len === 13 && candidate.startsWith('55')) {
        found.push(candidate);
      } else if (len === 12 && candidate.startsWith('55')) {
        found.push(candidate);
      } else if (len === 11 || len === 10) {
        found.push(`55${candidate}`);
      }
    }
  }
  return Array.from(new Set(found.filter(v => /^55\d{10,11}$/.test(v))));
}

function messageSuggestsRedirect(text) {
  const low = String(text || '').toLowerCase();
  return [
    'nao e comigo',
    'não é comigo',
    'nao sou eu',
    'não sou eu',
    'fale com',
    'pode falar com',
    'o responsavel e',
    'o responsável é',
    'segue o contato',
    'segue contato',
    'contato dele',
    'contato dela',
    'numero dele',
    'numero dela',
    'número dele',
    'número dela',
  ].some(token => low.includes(token));
}

function messageSuggestsPoliteUnknown(text) {
  const low = String(text || '').toLowerCase();
  return (
    (low.includes('nao conhe') || low.includes('não conhe')) &&
    (low.includes('obrigad') || low.includes('agradec'))
  );
}

async function notifyEduardoAlert(text) {
  if (!IRIS_ALERT_PHONE) return;
  const chatId = `${IRIS_ALERT_PHONE}@c.us`;
  rememberBotOutbound(chatId, text);
  await getActiveEngine().sendMessage(chatId, text);
}

function cloneFollowUpForRedirect(sourceDigits, newDigits, sourceMessage) {
  const followUps = safeReadJson(FOLLOW_UPS_FILE, {});
  const source = followUps[sourceDigits] || {};
  const nowIso = new Date().toISOString();
  followUps[newDigits] = {
    ...source,
    telefone: newDigits,
    sent_at: '',
    responded: false,
    responded_at: '',
    status: 'active',
    redirected_from: sourceDigits,
    redirected_from_message: sourceMessage,
    priority_redirect: true,
    priority_created_at: nowIso,
    sent_message: '',
    last_error: '',
  };
  safeWriteJson(FOLLOW_UPS_FILE, followUps);
  return followUps[newDigits];
}

function markRedirectedContactSent(newDigits, sourceDigits, message, sendResult) {
  const nowIso = new Date().toISOString();
  const followUps = safeReadJson(FOLLOW_UPS_FILE, {});
  const entry = followUps[newDigits] || {};
  entry.sent_at = nowIso;
  entry.sent_message = message;
  entry.last_error = '';
  entry.ack_confirmed = sendResult?.ackConfirmed !== false;
  entry.ack_warning = sendResult?.ackWarning || '';
  entry.send_attempts = Number(entry.send_attempts || 0) + 1;
  followUps[newDigits] = entry;
  safeWriteJson(FOLLOW_UPS_FILE, followUps);

  const sentMessages = safeReadJson(SENT_MESSAGES_FILE, {});
  sentMessages[newDigits] = {
    ...(sentMessages[newDigits] || {}),
    status: entry.ack_confirmed ? 'success' : 'success_without_ack',
    empresa: entry.empresa || '',
    redirected_from: sourceDigits,
    sent_at: nowIso,
    ...(entry.ack_warning ? { warning: entry.ack_warning } : {})
  };
  safeWriteJson(SENT_MESSAGES_FILE, sentMessages);

  outboundContacts.add(newDigits);
  irisInitiatedContacts.add(newDigits);
}

function markRedirectedContactPendingRetry(newDigits, errorText) {
  const followUps = safeReadJson(FOLLOW_UPS_FILE, {});
  const entry = followUps[newDigits] || {};
  entry.sent_at = '';
  entry.status = 'active';
  entry.last_error = errorText;
  entry.send_attempts = Number(entry.send_attempts || 0) + 1;
  followUps[newDigits] = entry;
  safeWriteJson(FOLLOW_UPS_FILE, followUps);
}

async function classifyOutboundReplyWithAI(fromDigits, merged) {
  const url = `${AI_SERVICE_URL.replace(/\/$/, '')}/classify-outbound-reply`;
  const followUps = safeReadJson(FOLLOW_UPS_FILE, {});
  const source = followUps[fromDigits] || {};
  const payload = {
    phone: fromDigits,
    message: merged,
    empresa: source.empresa || '',
    outbound_message: source.sent_message || source.base_message || '',
    source_context: {
      status: source.status || '',
      redirected_from: source.redirected_from || '',
      contato: source.contato || '',
      cargo: source.cargo || '',
    }
  };
  const res = await axios.post(url, payload, { timeout: 45000 });
  return res.data || {};
}

async function handleSpecialOutboundReply(fromDigits, merged) {
  const emails = extractEmails(merged);
  if (emails.length) {
    const thanks = 'Perfeito, obrigado pelo retorno. Vou encaminhar internamente.';
    const alertText = `Aviso Eduardo: o contato ${fromDigits} pediu seguimento por e-mail. E-mail informado: ${emails.join(', ')}.`;
    await notifyEduardoAlert(alertText);
    revokeAiForContact(fromDigits, 'email_redirect');
    return { handled: true, reply: thanks };
  }

  const candidatePhones = extractCandidatePhones(merged).filter(p => p !== fromDigits && p !== IRIS_ALERT_PHONE);
  // Se o contato enviou qualquer numero alternativo valido, redireciona automaticamente.
  // Nao depende de frase-gatilho para evitar perder casos como "nosso numero mudou".
  if (candidatePhones.length) {
    const newDigits = candidatePhones[0];
    const followUps = safeReadJson(FOLLOW_UPS_FILE, {});
    const empresa = String((followUps[fromDigits] || {}).empresa || 'empresa').trim();
    const message = outboundDefaultMessage(empresa);
    cloneFollowUpForRedirect(fromDigits, newDigits, merged);
    try {
      const sendResult = await sendHumanMessage(`${newDigits}@c.us`, message, {
        requireAck: true,
        ackTimeoutMs: 25000,
        allowMissingAck: true
      });
      rememberBotOutbound(`${newDigits}@c.us`, message);
      markRedirectedContactSent(newDigits, fromDigits, message, sendResult);
    } catch (redirectErr) {
      markRedirectedContactPendingRetry(newDigits, String(redirectErr.message || redirectErr));
      console.log('[REDIRECT] envio imediato falhou, mantido em prioridade para retry:', newDigits, '-', String(redirectErr.message || redirectErr));
    }
    revokeAiForContact(fromDigits, 'redirected_contact');
    return {
      handled: true,
      reply: 'Perfeito, obrigado por me encaminhar. Vou falar com esse contato.'
    };
  }

  if (messageSuggestsPoliteUnknown(merged)) {
    revokeAiForContact(fromDigits, 'polite_unknown');
    return {
      handled: true,
      reply: 'Perfeito, obrigado pelo retorno e pela atencao.'
    };
  }

  try {
    const aiDecision = await classifyOutboundReplyWithAI(fromDigits, merged);
    const decision = String(aiDecision.decision || '').toLowerCase();
    const confidence = Number(aiDecision.confidence || 0);
    if (decision === 'close' && confidence >= 70) {
      revokeAiForContact(fromDigits, 'non_icp_auto_attendant_ai');
      return {
        handled: true,
        reply: String(aiDecision.suggested_reply || 'Perfeito, muito obrigado pelo retorno e pela atencao. Por ora, nao vamos seguir por este canal. De toda forma, fico a disposicao caso faca sentido conversar no futuro.').trim()
      };
    };
  } catch (e) {
    console.log('[CLASSIFY_OUTBOUND_REPLY] erro:', String(e.message || e));
  }

  return { handled: false, reply: '' };
}

// Carrega lista de nÃºmeros que receberam broadcast outbound
const OUTBOUND_CONTACTS_FILE = path.resolve(__dirname, '..', 'outbound_data', 'sent_messages.json');
let outboundContacts = new Set();
const irisInitiatedContacts = new Set();
const manualTakeoverContacts = new Set();
const botRecentOutboundByContact = new Map();
const BOT_OUTBOUND_MATCH_WINDOW_MS = 20000;

function rememberBotOutbound(chatIdOrFrom, text) {
  const digits = onlyDigits(chatIdOrFrom);
  if (!digits) return;
  irisInitiatedContacts.add(digits);
  botRecentOutboundByContact.set(digits, {
    text: String(text || '').trim(),
    at: Date.now()
  });
}

function wasRecentBotOutbound(chatIdOrFrom, text) {
  const digits = onlyDigits(chatIdOrFrom);
  if (!digits) return false;
  const rec = botRecentOutboundByContact.get(digits);
  if (!rec) return false;
  const age = Date.now() - Number(rec.at || 0);
  if (age > BOT_OUTBOUND_MATCH_WINDOW_MS) return false;
  const msgText = String(text || '').trim();
  return rec.text === msgText;
}

function persistManualTakeoverForContact(digits, reason = 'manual_takeover') {
  const cleanDigits = onlyDigits(digits);
  if (!cleanDigits) return;
  const nowIso = new Date().toISOString();
  try {
    const sentMessages = safeReadJson(SENT_MESSAGES_FILE, {});
    const prev = sentMessages[cleanDigits] || {};
    sentMessages[cleanDigits] = {
      ...prev,
      status: reason,
      manual_takeover_at: nowIso
    };
    safeWriteJson(SENT_MESSAGES_FILE, sentMessages);
  } catch (e) {
    console.log('[MANUAL] erro ao persistir sent_messages para', cleanDigits, '-', String(e.message || e));
  }

  try {
    const followUps = safeReadJson(FOLLOW_UPS_FILE, {});
    if (followUps[cleanDigits]) {
      followUps[cleanDigits].status = reason;
      followUps[cleanDigits].manual_takeover_at = nowIso;
      safeWriteJson(FOLLOW_UPS_FILE, followUps);
    }
  } catch (e) {
    console.log('[MANUAL] erro ao persistir follow_ups para', cleanDigits, '-', String(e.message || e));
  }
}

function revokeAiForContact(digits, reason = 'manual_takeover') {
  const cleanDigits = onlyDigits(digits);
  if (!cleanDigits) return;
  irisInitiatedContacts.delete(cleanDigits);
  outboundContacts.delete(cleanDigits);
  manualTakeoverContacts.add(cleanDigits);
  persistManualTakeoverForContact(cleanDigits, reason);
}

function loadOutboundContacts() {
  try {
    if (fs.existsSync(OUTBOUND_CONTACTS_FILE)) {
      const data = safeReadJson(OUTBOUND_CONTACTS_FILE, {});
      // Carrega envios confirmados/persistidos no controle principal.
      for (const phone in data) {
        const status = String((data[phone] || {}).status || '').toLowerCase();
        const digits = String(phone).replace(/\D/g, '');
        if (!digits) continue;
        if (status === 'manual_takeover') {
          manualTakeoverContacts.add(digits);
          continue;
        }
        if (status === 'success' || status === 'success_without_ack') {
          outboundContacts.add(digits);
          irisInitiatedContacts.add(digits);
        }
      }
    }

    const followUps = safeReadJson(FOLLOW_UPS_FILE, {});
    const phones = Object.keys(followUps || {});
    let loaded = 0;
    for (const phone of phones) {
      const entry = followUps[phone] || {};
      const sentAt = String(entry.sent_at || '').trim();
      if (sentAt) {
        const digits = String(phone).replace(/\D/g, '');
        outboundContacts.add(digits);
        irisInitiatedContacts.add(digits);
        loaded += 1;
      }
    }
    if (loaded) {
      console.log('[OUTBOUND] Carregados', loaded, 'nÃºmeros adicionais a partir de follow_ups.json');
    }
    console.log('[OUTBOUND] Total de números autorizados por outbound:', irisInitiatedContacts.size);
  } catch (e) {
    console.log('[OUTBOUND] Erro ao carregar contatos outbound:', e.message);
  }
}

// Carrega contatos outbound na inicializaÃ§Ã£o
loadOutboundContacts();

function refreshOutboundContactAuthorization(digits) {
  const cleanDigits = onlyDigits(digits);
  if (!cleanDigits || manualTakeoverContacts.has(cleanDigits)) return false;
  if (irisInitiatedContacts.has(cleanDigits)) return true;

  try {
    const sentMessages = safeReadJson(SENT_MESSAGES_FILE, {});
    const sentEntry = sentMessages[cleanDigits] || {};
    const sentStatus = String(sentEntry.status || '').toLowerCase();
    if (sentStatus === 'success' || sentStatus === 'success_without_ack') {
      outboundContacts.add(cleanDigits);
      irisInitiatedContacts.add(cleanDigits);
      console.log('[ALLOW] autorizado dinamicamente via sent_messages:', cleanDigits);
      return true;
    }
  } catch (e) {
    console.log('[ALLOW] erro ao consultar sent_messages:', String(e.message || e));
  }

  try {
    const followUps = safeReadJson(FOLLOW_UPS_FILE, {});
    const entry = followUps[cleanDigits] || {};
    const sentAt = String(entry.sent_at || '').trim();
    const status = String(entry.status || '').toLowerCase();
    if (sentAt && status !== 'manual_takeover') {
      outboundContacts.add(cleanDigits);
      irisInitiatedContacts.add(cleanDigits);
      console.log('[ALLOW] autorizado dinamicamente via follow_ups:', cleanDigits);
      return true;
    }
  } catch (e) {
    console.log('[ALLOW] erro ao consultar follow_ups:', String(e.message || e));
  }

  return false;
}

function isAllowed(from) {
  const digits = onlyDigits(from);
  if (!digits) {
    console.log('[ALLOW] blocked_invalid_number');
    return false;
  }

  if (manualTakeoverContacts.has(digits)) {
    console.log('[ALLOW] blocked_manual_takeover:', digits);
    return false;
  }

  if (irisInitiatedContacts.has(digits) || refreshOutboundContactAuthorization(digits)) {
    return true;
  }

  console.log('[ALLOW] blocked_not_in_iris_started_contacts:', digits);
  return false;
}

function splitReply(text, maxLen = 1000) {
  const t = String(text || '').trim();
  if (!t) return [];

  // Split by double newlines (paragraphs)
  let initialParts = t.split(/\n\n+/);

  let finalParts = [];
  for (const part of initialParts) {
    const p = part.trim();
    if (!p) continue;

    // If a part is still too long, split by single newline if it helps
    if (p.length > maxLen) {
      const subParts = p.split(/\n+/);
      for (const sub of subParts) {
        const s = sub.trim();
        if (!s) continue;

        if (s.length > maxLen) {
          let i = 0;
          while (i < s.length) {
            finalParts.push(s.slice(i, i + maxLen));
            i += maxLen;
          }
        } else {
          finalParts.push(s);
        }
      }
    } else {
      finalParts.push(p);
    }
  }
  return finalParts;
}

async function forwardToAI(message, fromDigits) {
  const url = `${AI_SERVICE_URL.replace(/\/$/, '')}/reply`;
  const payload = { message, from: fromDigits, contact_id: fromDigits };
  console.log('[FORWARD_TO_AI] Enviando para:', url, 'Payload:', JSON.stringify(payload));
  try {
    const res = await axios.post(url, payload, { timeout: 120000 }); // Aumentado para 120s para agendamentos
    console.log('[FORWARD_TO_AI] Resposta recebida:', res.status, res.data?.reply?.substring(0, 100) || 'sem reply');
    return res.data && res.data.reply ? res.data.reply : '';
  } catch (error) {
    console.error('[FORWARD_TO_AI] Erro:', error.message);
    if (error.response) {
      console.error('[FORWARD_TO_AI] Status:', error.response.status);
      console.error('[FORWARD_TO_AI] Data:', error.response.data);
    }
    throw error;
  }
}

async function transcribeAudio(audioB64, mime) {
  const url = `${AI_SERVICE_URL.replace(/\/$/, '')}/transcribe`;
  const payload = { audio_b64: audioB64, audio_mime: mime };
  const res = await axios.post(url, payload, { timeout: 60000 });
  return (res.data && res.data.text) ? res.data.text : '';
}

const sessionPath = path.resolve(PROJECT_ROOT, '.wwebjs_auth');
const WPP_SESSION_ID = CONFIG.whatsapp?.session_id || process.env.WPP_SESSION_ID || 'default';
if (RESET_SESSION) {
  try {
    const sessionDir = path.resolve(sessionPath, `session-${WPP_SESSION_ID}`);
    if (fs.existsSync(sessionDir)) {
      fs.rmSync(sessionDir, { recursive: true, force: true });
      console.log('[SESSION] Removida sessÃ£o:', sessionDir);
    }
  } catch (e) {
    console.log('[SESSION] Falha ao remover sessÃ£o:', String(e && e.message || e));
  }
  try {
    const baileysAuthDir = path.resolve(process.cwd(), '.baileys_auth');
    if (fs.existsSync(baileysAuthDir)) {
      fs.rmSync(baileysAuthDir, { recursive: true, force: true });
      console.log('[SESSION] Removida sessÃ£o Baileys:', baileysAuthDir);
    }
  } catch (e) {
    console.log('[SESSION] Falha ao remover sessÃ£o Baileys:', String(e && e.message || e));
  }
  try {
    if (fs.existsSync(WHATSAPP_READY_FILE)) {
      fs.rmSync(WHATSAPP_READY_FILE, { force: true });
      console.log('[SESSION] Status de prontidÃ£o limpo:', WHATSAPP_READY_FILE);
    }
  } catch (e) {
    console.log('[SESSION] Falha ao limpar status de prontidÃ£o:', String(e && e.message || e));
  }
}

// Tenta encontrar o Chrome no Windows
function findChromePath() {
  if (process.env.CHROME_PATH) {
    console.log('[PUPPETEER] Usando CHROME_PATH do .env:', process.env.CHROME_PATH);
    return process.env.CHROME_PATH;
  }

  // Caminhos comuns do Chrome no Windows
  const chromePaths = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe'
  ];

  // Adiciona caminhos baseados em variÃ¡veis de ambiente se disponÃ­veis
  if (process.env.LOCALAPPDATA) {
    chromePaths.push(process.env.LOCALAPPDATA + '\\Google\\Chrome\\Application\\chrome.exe');
  }
  if (process.env.PROGRAMFILES) {
    chromePaths.push(process.env.PROGRAMFILES + '\\Google\\Chrome\\Application\\chrome.exe');
  }
  if (process.env['PROGRAMFILES(X86)']) {
    chromePaths.push(process.env['PROGRAMFILES(X86)'] + '\\Google\\Chrome\\Application\\chrome.exe');
  }

  console.log('[PUPPETEER] Procurando Chrome nos caminhos:', chromePaths);

  for (const chromePath of chromePaths) {
    try {
      if (chromePath && fs.existsSync(chromePath)) {
        console.log('[PUPPETEER] âœ… Chrome encontrado em:', chromePath);
        return chromePath;
      }
    } catch (e) {
      // Ignora erros ao verificar caminho
    }
  }

  console.log('[PUPPETEER] âš ï¸ Chrome nÃ£o encontrado, tentando usar Chromium do Puppeteer');
  return undefined;
}

const USE_PUPPETEER_DEFAULT = String(process.env.USE_PUPPETEER_DEFAULT || '').toLowerCase() === 'true';
const chromePath = USE_PUPPETEER_DEFAULT ? undefined : findChromePath();
console.log('[PUPPETEER] Caminho do Chrome a ser usado:', chromePath || 'Chromium padrÃ£o do Puppeteer (auto)');
console.log('[PUPPETEER] Headless:', HEADLESS);

const ENGINE_OPTIONS = {
  sessionPath,
  sessionId: WPP_SESSION_ID,
  headless: HEADLESS,
  chromePath,
  usePuppeteerDefault: USE_PUPPETEER_DEFAULT
};

const BAILEYS_AUTH_FILE = path.resolve(process.cwd(), '.baileys_auth', 'baileys.json');

const sessions = new Map();
const processedInboundIds = new Map();
const blockedLogCooldown = new Map();
const WAIT_BASE_MS = 2000;
const TYPING_GRACE_MAX_MS = 180000;
const TYPING_RECENT_MS = 20000;
const INACTIVITY_WINDOW_MS = 12000;
const TYPING_CHECK_MS = 3000;
const PROCESSED_ID_TTL_MS = 10 * 60 * 1000;
const BLOCKED_LOG_COOLDOWN_MS = 60 * 1000;

function appendInboundAudit(event) {
  try {
    const line = JSON.stringify({
      at: new Date().toISOString(),
      ...(event || {})
    });
    fs.appendFileSync(INBOUND_AUDIT_FILE, `${line}\n`, 'utf8');
  } catch {}
}

function serializeSessions() {
  const out = {};
  for (const [chatId, session] of sessions.entries()) {
    out[chatId] = {
      buffer: Array.isArray(session.buffer) ? session.buffer : [],
      bufferMessageIds: Array.isArray(session.bufferMessageIds) ? session.bufferMessageIds : [],
      startedAt: Number(session.startedAt || 0),
      lastTyping: Number(session.lastTyping || 0),
      lastMsgAt: Number(session.lastMsgAt || 0),
      immediate: Boolean(session.immediate),
      processing: false
    };
  }
  return out;
}

function persistInboundSessions() {
  safeWriteJson(INBOUND_SESSIONS_FILE, serializeSessions());
}

function loadInboundSessions() {
  const raw = safeReadJson(INBOUND_SESSIONS_FILE, {});
  if (!raw || typeof raw !== 'object') return;
  for (const [chatId, session] of Object.entries(raw)) {
    sessions.set(chatId, {
      buffer: Array.isArray(session.buffer) ? session.buffer : [],
      bufferMessageIds: Array.isArray(session.bufferMessageIds) ? session.bufferMessageIds : [],
      timer: null,
      startedAt: Number(session.startedAt || 0),
      lastTyping: Number(session.lastTyping || 0),
      lastMsgAt: Number(session.lastMsgAt || 0),
      immediate: Boolean(session.immediate),
      processing: false
    });
  }
}

function readInboundQueue() {
  const queue = safeReadJson(INBOUND_QUEUE_FILE, []);
  return Array.isArray(queue) ? queue : [];
}

function writeInboundQueue(queue) {
  return safeWriteJson(INBOUND_QUEUE_FILE, Array.isArray(queue) ? queue : []);
}

function upsertInboundQueueItem(item) {
  const queue = readInboundQueue();
  const idx = queue.findIndex((entry) => String(entry.id || '') === String(item.id || ''));
  if (idx >= 0) {
    queue[idx] = { ...queue[idx], ...item };
  } else {
    queue.push(item);
  }
  writeInboundQueue(queue);
}

function updateInboundQueueItem(itemId, patch) {
  const queue = readInboundQueue();
  const idx = queue.findIndex((entry) => String(entry.id || '') === String(itemId || ''));
  if (idx < 0) return;
  queue[idx] = { ...queue[idx], ...(patch || {}) };
  writeInboundQueue(queue);
}

function removeInboundQueueItem(itemId) {
  const queue = readInboundQueue().filter((entry) => String(entry.id || '') !== String(itemId || ''));
  writeInboundQueue(queue);
}

function extractInboundMessageId(msg) {
  const id = msg?.raw?.key?.id;
  if (!id) return null;
  return String(id);
}

function buildInboundQueueId(fromDigits, merged, sourceIds = []) {
  const sourceKey = Array.isArray(sourceIds) && sourceIds.length
    ? sourceIds.map((v) => String(v || '').trim()).filter(Boolean).sort().join('|')
    : `${fromDigits}:${stableHash(merged)}`;
  return `inbound:${fromDigits}:${sourceKey}`;
}

function markOutboundResponse(fromDigits, merged) {
  try {
    const followUpsFile = path.resolve(__dirname, '..', 'outbound_data', 'follow_ups.json');
    if (fs.existsSync(followUpsFile)) {
      const followUps = safeReadJson(followUpsFile, {});
      if (followUps[fromDigits]) {
        followUps[fromDigits].responded = true;
        followUps[fromDigits].responded_at = new Date().toISOString();
        followUps[fromDigits].status = 'responded';
        fs.writeFileSync(followUpsFile, JSON.stringify(followUps, null, 2), 'utf8');
        console.log('[OUTBOUND] Resposta registrada para', fromDigits);
      }
    }

    const responsesFile = path.resolve(__dirname, '..', 'outbound_data', 'responses_tracking.json');
    const responses = safeReadJson(responsesFile, {});
    responses[fromDigits] = {
      responded_at: new Date().toISOString(),
      first_message: merged
    };
    fs.writeFileSync(responsesFile, JSON.stringify(responses, null, 2), 'utf8');
  } catch (e) {
    console.log('[OUTBOUND] Erro ao registrar resposta:', e.message);
  }
}

function enqueueInbound(chatId, fromDigits, merged, sourceIds = []) {
  const item = {
    id: buildInboundQueueId(fromDigits, merged, sourceIds),
    chat_id: chatId,
    from_digits: fromDigits,
    message: merged,
    source_ids: Array.isArray(sourceIds) ? sourceIds : [],
    status: 'pending',
    attempts: 0,
    queued_at: new Date().toISOString(),
    last_error: '',
    processing_started_at: null
  };
  upsertInboundQueueItem(item);
  appendInboundAudit({
    type: 'queue_enqueued',
    item_id: item.id,
    chat_id: chatId,
    from_digits: fromDigits,
    source_ids: item.source_ids,
    message_preview: String(merged || '').slice(0, 160)
  });
  return item;
}

async function processInboundQueue() {
  if (inboundQueueLock || !WPP_READY || !ENGINE_READY) return;
  inboundQueueLock = true;
  try {
    while (true) {
      const queue = readInboundQueue();
      const next = queue.find((item) => ['pending', 'error'].includes(String(item.status || '')));
      if (!next) break;

      updateInboundQueueItem(next.id, {
        status: 'processing',
        processing_started_at: new Date().toISOString(),
        attempts: Number(next.attempts || 0) + 1
      });
      appendInboundAudit({
        type: 'queue_processing',
        item_id: next.id,
        chat_id: next.chat_id,
        from_digits: next.from_digits,
        attempt: Number(next.attempts || 0) + 1
      });

      try {
        const fromDigits = onlyDigits(next.from_digits);
        const chatId = String(next.chat_id || `${fromDigits}@c.us`);
        const merged = String(next.message || '').trim();
        if (!fromDigits || !merged) {
          removeInboundQueueItem(next.id);
          appendInboundAudit({
            type: 'queue_dropped_invalid',
            item_id: next.id,
            chat_id: chatId,
            from_digits: fromDigits
          });
          continue;
        }

        setPendingMessage(fromDigits, merged);

        const specialHandling = await handleSpecialOutboundReply(fromDigits, merged);
        if (specialHandling.handled) {
          clearPendingMessage(fromDigits);
          markOutboundResponse(fromDigits, merged);
          if (specialHandling.reply) {
            addToHistory(fromDigits, 'assistant', specialHandling.reply);
            rememberBotOutbound(chatId, specialHandling.reply);
            await getActiveEngine().sendMessage(chatId, specialHandling.reply);
          }
          removeInboundQueueItem(next.id);
          appendInboundAudit({
            type: 'queue_processed_special',
            item_id: next.id,
            chat_id: chatId,
            from_digits: fromDigits
          });
          continue;
        }

        let aiReply = '';
        let attempts = 0;
        const maxAttempts = 2;
        while (attempts < maxAttempts) {
          try {
            attempts += 1;
            aiReply = await forwardToAI(merged, fromDigits);
            break;
          } catch (retryErr) {
            console.log('[RETRY]', fromDigits, 'tentativa', attempts, 'de', maxAttempts, '-', retryErr.message);
            if (attempts >= maxAttempts) throw retryErr;
            await delay(2000);
          }
        }

        clearPendingMessage(fromDigits);
        addToHistory(fromDigits, 'assistant', aiReply);
        markOutboundResponse(fromDigits, merged);

        const finalMessage = aiReply.trim().replace(/\n{3,}/g, '\n\n');
        const base = 500;
        const perChar = 50;
        const max = 10000;
        const jitter = Math.floor(Math.random() * 500);
        const typingDelay = Math.min(base + finalMessage.length * perChar + jitter, max);
        await delay(typingDelay);

        console.log('[OUT]', fromDigits, finalMessage.substring(0, 100) + '...');
        rememberBotOutbound(chatId, finalMessage);
        await getActiveEngine().sendMessage(chatId, finalMessage);

        removeInboundQueueItem(next.id);
        appendInboundAudit({
          type: 'queue_processed_ai',
          item_id: next.id,
          chat_id: chatId,
          from_digits: fromDigits,
          reply_preview: finalMessage.slice(0, 160)
        });
      } catch (e) {
        updateInboundQueueItem(next.id, {
          status: 'error',
          last_error: String(e.message || e),
          last_error_at: new Date().toISOString()
        });
        appendInboundAudit({
          type: 'queue_error',
          item_id: next.id,
          chat_id: next.chat_id,
          from_digits: next.from_digits,
          error: String(e.message || e)
        });
        console.log('[INBOUND_QUEUE] erro ao processar item', next.id, '-', String(e.message || e));
        break;
      }
    }
  } finally {
    inboundQueueLock = false;
  }
}

function enqueueRecoveredSessions() {
  const now = Date.now();
  for (const [chatId, session] of sessions.entries()) {
    const buffer = Array.isArray(session.buffer) ? session.buffer.filter(Boolean) : [];
    if (!buffer.length) continue;
    const lastMsgAt = Number(session.lastMsgAt || 0);
    if (lastMsgAt && (now - lastMsgAt) < WAIT_BASE_MS) continue;
    const fromDigits = onlyDigits(chatId);
    if (!fromDigits) continue;
    const merged = buffer.join('. ');
    const item = enqueueInbound(chatId, fromDigits, merged, []);
    session.buffer = [];
    session.startedAt = 0;
    session.processing = false;
    session.timer = null;
    appendInboundAudit({
      type: 'queue_recovered_session',
      item_id: item.id,
      chat_id: chatId,
      from_digits: fromDigits
    });
  }
  persistInboundSessions();
}

function shouldIgnoreDuplicateInbound(msg) {
  const id = extractInboundMessageId(msg);
  if (!id) return false;
  const now = Date.now();
  const prev = processedInboundIds.get(id);
  processedInboundIds.set(id, now);
  if (processedInboundIds.size > 2000) {
    for (const [k, ts] of processedInboundIds.entries()) {
      if (now - ts > PROCESSED_ID_TTL_MS) processedInboundIds.delete(k);
    }
  }
  return Boolean(prev && (now - prev) <= PROCESSED_ID_TTL_MS);
}

function shouldLogBlocked(fromDigits) {
  const now = Date.now();
  const key = String(fromDigits || 'unknown');
  const last = blockedLogCooldown.get(key) || 0;
  if ((now - last) < BLOCKED_LOG_COOLDOWN_MS) return false;
  blockedLogCooldown.set(key, now);
  return true;
}

function onOutgoingMessage(msg) {
  try {
    const toDigits = onlyDigits(msg && msg.from);
    if (!toDigits) return;
    const body = String((msg && msg.body) || '').trim();
    if (wasRecentBotOutbound(msg.from, body)) {
      return;
    }
    if (body) {
      addToHistory(toDigits, 'manual_outgoing', body);
    }
    revokeAiForContact(toDigits, 'manual_takeover');
    const chatId = String((msg && msg.from) || '');
    const s = sessions.get(chatId);
    if (s) {
      if (s.timer) clearTimeout(s.timer);
      s.timer = null;
      s.buffer = [];
      s.bufferMessageIds = [];
      s.processing = false;
      persistInboundSessions();
    }
    clearPendingMessage(toDigits);
    console.log('[MANUAL] takeover_detected_for_contact_removed_from_ai_base:', toDigits);
  } catch (e) {
    console.log('[MANUAL] erro ao processar outgoing_message:', String(e.message || e));
  }
}

async function onMessage(msg) {
  try {
    if (!String(msg.from || '').endsWith('@c.us')) return;
    const fromDigits = onlyDigits(msg.from);
    if (shouldIgnoreDuplicateInbound(msg)) return;
    const inboundText = String(msg.body || '').trim();
    if (fromDigits && inboundText) {
      addToHistory(fromDigits, 'user', inboundText);
    }
    if (!isAllowed(msg.from)) {
      if (shouldLogBlocked(fromDigits)) {
        console.log('[BLOCKED] incoming_message_from_non_base_ignored');
      }
      return;
    }
    console.log('[MSG] Recebida de:', fromDigits, 'Mensagem:', inboundText.slice(0, 120));
    console.log('[ALLOWED]', fromDigits, 'permitido, processando mensagem...');

    // Comando especial: !reiniciar
    const msgBody = inboundText.toLowerCase();
    if (msgBody === '!reiniciar' || msgBody === '!restart') {
      console.log('[RESTART]', fromDigits, 'solicitou reinÃ­cio');
      try {
        await new Promise(r => setTimeout(r, 1000));
        const restartText = 'Reiniciando o bot...';
        rememberBotOutbound(msg.from, restartText);
        await getActiveEngine().sendMessage(msg.from, restartText);


        // Limpa sessÃµes e reinicia
        sessions.clear();
        persistInboundSessions();
        setTimeout(() => {
          console.log('[RESTART] Reiniciando cliente...');
          restartEngine(0);
        }, 1000);
      } catch (e) {
        console.log('[RESTART] Erro:', e.message);
      }
      return;
    }

    let s = sessions.get(msg.from);
    if (!s) {
      s = { buffer: [], bufferMessageIds: [], timer: null, startedAt: 0, lastTyping: 0, lastMsgAt: 0, immediate: false, processing: false };
      sessions.set(msg.from, s);
      persistInboundSessions();
    }

    // Se jÃ¡ estÃ¡ processando, adiciona ao buffer e retorna (nÃ£o processa novamente)
    if (s.processing) {
      s.buffer.push(String(msg.body || '').trim());
      s.lastMsgAt = Date.now();
      persistInboundSessions();
      console.log('[BUFFER]', fromDigits, 'mensagem adicionada ao buffer (processando)');
      return;
    }

    s.buffer.push(String(msg.body || '').trim());
    s.lastMsgAt = Date.now();
    persistInboundSessions();
    // NÃƒO mostra "digitando" imediatamente - espera agrupar todas as mensagens primeiro
    if (msg.hasMedia && (msg.type === 'audio' || msg.type === 'ptt')) {
      try {
        const media = await msg.downloadMedia();
        const t = await transcribeAudio(media.data, media.mimetype);
        if (t) s.buffer.push(t);
        persistInboundSessions();
        console.log('[TRANSCRIBE]', fromDigits, String(t || '').slice(0, 200));
        s.immediate = true;
      } catch (e) {
        console.log('ERROR', String(e.message || e));
        s.immediate = true;
      }
    }
    if (msg.hasMedia && (msg.type === 'image' || msg.type === 'video')) {
      return;
    }
    if (s.timer) clearTimeout(s.timer);

    const schedule = async () => {
      try {
        // Verifica se hÃ¡ novas mensagens desde que o timer foi agendado
        const now = Date.now();
        const sinceLastMsg = s.lastMsgAt ? (now - s.lastMsgAt) : INACTIVITY_WINDOW_MS;
        const timeSinceTyping = s.lastTyping ? (now - s.lastTyping) : TYPING_GRACE_MAX_MS;

        // Se passou menos de 3s desde a Ãºltima mensagem, espera para ver se vai digitar
        if (sinceLastMsg < TYPING_CHECK_MS) {
          const waitForTyping = TYPING_CHECK_MS - sinceLastMsg;
          console.log('[WAIT_TYPING]', fromDigits, 'esperando', Math.round(waitForTyping / 1000), 's para verificar se vai digitar');
          s.timer = setTimeout(schedule, waitForTyping);
          return;
        }

        // ApÃ³s 3s, verifica se estÃ¡ digitando
        const isTyping = s.lastTyping && timeSinceTyping < TYPING_GRACE_MAX_MS;
        let waitMs = 0;

        if (isTyping) {
          // UsuÃ¡rio estÃ¡ digitando - espera atÃ© 3 minutos desde o Ãºltimo typing
          waitMs = Math.max(0, TYPING_GRACE_MAX_MS - timeSinceTyping);
          console.log('[TYPING]', fromDigits, 'usuÃ¡rio digitando, esperando mais', Math.round(waitMs / 1000), 's');
        } else {
          // NÃ£o estÃ¡ digitando - espera tempo normal para agrupar mensagens (12s)
          waitMs = Math.max(0, INACTIVITY_WINDOW_MS - sinceLastMsg);
          console.log('[WAIT_GROUP]', fromDigits, 'esperando', Math.round(waitMs / 1000), 's para agrupar mensagens');
        }

        if (waitMs > 0) {
          s.timer = setTimeout(schedule, Math.min(waitMs, 3000)); // Verifica a cada 3s no mÃ¡ximo
          return;
        }

        // Verifica novamente se chegou nova mensagem enquanto esperava
        const finalCheck = Date.now();
        const finalSinceLastMsg = s.lastMsgAt ? (finalCheck - s.lastMsgAt) : INACTIVITY_WINDOW_MS;
        if (finalSinceLastMsg < INACTIVITY_WINDOW_MS) {
          // Ainda hÃ¡ mensagens chegando, espera mais
          const remainingWait = INACTIVITY_WINDOW_MS - finalSinceLastMsg;
          console.log('[WAIT_MORE]', fromDigits, 'nova mensagem detectada, esperando mais', Math.round(remainingWait / 1000), 's');
          s.timer = setTimeout(schedule, Math.min(remainingWait, 3000));
          return;
        }

        // Agora sim, processa todas as mensagens do buffer
        if (s.buffer.length === 0) {
          console.log('[SKIP]', fromDigits, 'buffer vazio');
          return;
        }

        s.processing = true; // Marca como processando para evitar processamento duplicado
        const merged = s.buffer.filter(Boolean).join('. ');
        const sourceIds = s.bufferMessageIds || [];
        s.buffer = [];
        s.bufferMessageIds = [];
        s.startedAt = 0;
        persistInboundSessions();
        const item = enqueueInbound(msg.from, fromDigits, merged, sourceIds);
        appendInboundAudit({
          type: 'message_buffer_flushed',
          item_id: item.id,
          chat_id: msg.from,
          from_digits: fromDigits,
          source_ids: sourceIds
        });
        s.processing = false;
        await processInboundQueue();
      } catch (e) {
        console.log('ERROR', String(e.message || e));
        // MantÃ©m mensagem pendente para retry no prÃ³ximo reinÃ­cio
        console.log('[PERSIST] Mensagem pendente salva para', fromDigits);
        s.processing = false; // Libera mesmo em caso de erro
        persistInboundSessions();
      }
    };
    const msgId = extractInboundMessageId(msg);
    s.bufferMessageIds = Array.isArray(s.bufferMessageIds) ? s.bufferMessageIds : [];
    if (msgId && !s.bufferMessageIds.includes(msgId)) {
      s.bufferMessageIds.push(msgId);
      persistInboundSessions();
    }
    s.timer = setTimeout(schedule, WAIT_BASE_MS);
  } catch (e) {
    console.log('ERROR', String(e.message || e));
  }
}

function onTyping(chat) {
  try {
    const id = chat && (typeof chat.id === 'string' ? chat.id : (chat.id && chat.id._serialized));
    if (!id) return;
    const s = sessions.get(id);
    if (s) {
      s.lastTyping = Date.now();
      const fromDigits = onlyDigits(id);
      console.log('[TYPING]', fromDigits, 'usuÃ¡rio estÃ¡ digitando - esperando atÃ© 3min');
      // Reseta o timer para esperar mais quando detecta typing
      if (s.timer) {
        clearTimeout(s.timer);
        // Reagenda verificando typing a cada 2 segundos
        const checkTyping = async () => {
          const now = Date.now();
          const isTyping = s.lastTyping && (now - s.lastTyping) < TYPING_GRACE_MAX_MS;
          if (isTyping) {
            // Ainda estÃ¡ digitando, verifica novamente em 2s
            s.timer = setTimeout(checkTyping, 2000);
          } else {
            // Parou de digitar, processa normalmente
            s.timer = null;
            const schedule = async () => {
              try {
                const now2 = Date.now();
                const sinceLastMsg = s.lastMsgAt ? (now2 - s.lastMsgAt) : INACTIVITY_WINDOW_MS;
                const waitMs = Math.max(0, INACTIVITY_WINDOW_MS - sinceLastMsg);
                if (waitMs > 0) {
                  s.timer = setTimeout(schedule, waitMs);
                  return;
                }
                const merged = s.buffer.filter(Boolean).join('. ');
                const sourceIds = Array.isArray(s.bufferMessageIds) ? s.bufferMessageIds : [];
                s.buffer = [];
                s.bufferMessageIds = [];
                s.startedAt = 0;
                persistInboundSessions();
                const item = enqueueInbound(id, fromDigits, merged, sourceIds);
                appendInboundAudit({
                  type: 'typing_buffer_flushed',
                  item_id: item.id,
                  chat_id: id,
                  from_digits: fromDigits,
                  source_ids: sourceIds
                });
                await processInboundQueue();
              } catch (e) {
                console.log('[SCHEDULE_ERROR]', e.message);
              }
            };
            schedule();
          }
        };
        s.timer = setTimeout(checkTyping, 2000);
      }
    }
  } catch (e) {
    console.log('[TYPING_ERROR]', e.message);
  }
}

function handleLoadingScreen(percent, message) {
  console.log('LOADING_SCREEN', percent, message);
}

function handleQr(qr) {
  setWhatsAppReady(false, { event: 'qr' });
  safeWriteJson(WHATSAPP_QR_FILE, {
    qr: String(qr || ''),
    generated_at: new Date().toISOString(),
    event: 'qr'
  });
  process.stdout.write('\n\n==================== QR CODE ====================\n');
  process.stdout.write('Escaneie: WhatsApp > Aparelhos conectados > Conectar um aparelho\n\n');
  qrcode.generate(qr, { small: true }, (qrStr) => {
    process.stdout.write(qrStr + '\n');
    process.stdout.write('=================================================\n\n');
  });
}

async function handleReady() {
  console.log('BOT_READY');
  cancelFallbackTimer();
  const selfId = (activeEngine && typeof activeEngine.getSelfId === 'function')
    ? activeEngine.getSelfId()
    : null;
  console.log('[READY] Conta conectada:', selfId || 'n/a');
  console.log('[READY] ALLOWED_CONTACTS no ready:', ALLOWED_CONTACTS);
  console.log('[READY] Total de nÃºmeros permitidos:', ALLOWED_CONTACTS.length);
  setWhatsAppReady(true, { event: 'ready', self_id: selfId || null, engine: activeEngine ? activeEngine.getId() : 'n/a' });
  safeWriteJson(WHATSAPP_QR_FILE, {
    qr: '',
    generated_at: new Date().toISOString(),
    event: 'ready'
  });
  WPP_READY = true;
  ENGINE_READY = true;
  if (AUTO_GREETING_ON_READY && ALLOWED_CONTACTS.length) {
    sendGreetings();
    scheduleStartupGreetings();
  }

  console.log('[PATCH] Aplicando patch de seguranÃ§a no navegador...');
  try {
    await getActiveEngine().applyBrowserPatch();
  } catch (e) {
    console.log('[PATCH] Erro ao aplicar patch:', e.message);
  }

  try {
    await sendPendingOutboundMessages();
  } catch { }
  loadInboundSessions();
  enqueueRecoveredSessions();
  processInboundQueue().catch(() => {});
  ensureOutboundScheduler();
}

function handleAuthenticated() {
  const selfId = (activeEngine && typeof activeEngine.getSelfId === 'function')
    ? activeEngine.getSelfId()
    : null;
  setWhatsAppReady(true, { event: 'authenticated', self_id: selfId || null, engine: activeEngine ? activeEngine.getId() : 'n/a' });
  WPP_READY = true;
  ENGINE_READY = false;
  console.log('[AUTH] Autenticado com sucesso; iniciando scheduler outbound');
  if (activeEngine && activeEngine.getId() === 'wwebjs') {
    getActiveEngine().applyBrowserPatch().catch(() => {});
    // Em alguns cenÃ¡rios o evento "ready" do wwebjs nÃ£o chega; destrava envio apÃ³s aquecer sessÃ£o.
    setTimeout(() => {
      if (activeEngine && activeEngine.getId() === 'wwebjs' && WPP_READY && !ENGINE_READY) {
        console.log('[AUTH] Fallback de ready (wwebjs): habilitando envio outbound');
        ENGINE_READY = true;
        sendPendingOutboundMessages().catch(() => {});
      }
    }, 7000);
  }
  if (AUTO_GREETING_ON_READY) {
    sendGreetings();
    scheduleStartupGreetings();
  }
  ensureOutboundScheduler();
}

function ensureOutboundScheduler() {
  if (outboundInterval) {
    try { clearInterval(outboundInterval); } catch { }
    outboundInterval = null;
  }
  outboundInterval = setInterval(() => {
    sendPendingOutboundMessages().catch(() => { });
    processInboundQueue().catch(() => { });
  }, 20000);
}

function handleAuthFailure() {
  console.log('AUTH_FAILURE');
}

function handleDisconnected(reason) {
  disconnectCount += 1;
  console.log('DISCONNECTED', String(reason || ''));
  setWhatsAppReady(false, { event: 'disconnected', reason: String(reason || '') });
  safeWriteJson(WHATSAPP_QR_FILE, {
    qr: '',
    generated_at: new Date().toISOString(),
    event: 'disconnected'
  });
  WPP_READY = false;
  ENGINE_READY = false;
  const isBaileys = activeEngine && activeEngine.getId() === 'baileys';
  const activeEngineId = activeEngine ? activeEngine.getId() : 'unknown';
  const reasonText = String(reason && reason.message ? reason.message : reason || '');
  const statusCode = reason && typeof reason === 'object' ? reason.statusCode : null;
  const disconnectReason = reason && typeof reason === 'object' ? reason.disconnectReason : null;
  const disconnectLocation =
    reason && typeof reason === 'object' && reason.lastDisconnect
      ? reason.lastDisconnect?.error?.data?.location
      : null;
  console.log(
    '[DIAG][DISCONNECT]',
    'count=', disconnectCount,
    '| engine=', activeEngineId,
    '| statusCode=', statusCode ?? 'n/a',
    '| reason=', disconnectReason ?? 'n/a',
    '| location=', disconnectLocation ?? 'n/a',
    '| message=', reasonText || 'n/a'
  );
  const shouldImmediateFallback =
    isBaileys && (
      statusCode === 405 ||
      disconnectReason === '405' ||
      reasonText.includes('Connection Failure')
    );

  if (shouldImmediateFallback) {
    const now = Date.now();
    baileys405Timestamps = baileys405Timestamps
      .filter((t) => (now - t) <= BAILEYS_405_WINDOW_MS);
    baileys405Timestamps.push(now);
    const retryCountWindow = baileys405Timestamps.length;
    console.log(
      '[DIAG][BAILEYS_405]',
      'windowCount=', retryCountWindow,
      '| windowMs=', BAILEYS_405_WINDOW_MS,
      '| cooldownMs=', BAILEYS_405_COOLDOWN_MS
    );

    if (retryCountWindow >= BAILEYS_405_MAX_RETRIES) {
      console.log('[DIAG][BAILEYS_405] Limite atingido. Aplicando cooldown antes de nova tentativa.');
      setTimeout(() => {
        restartEngine(0, currentFactoryIndex);
      }, BAILEYS_405_COOLDOWN_MS);
      return;
    }

    if (!ENABLE_WWEBJS_FALLBACK) {
      console.log('[FALLBACK] 405 detectado, mas fallback para wwebjs esta desabilitado. Reiniciando Baileys...');
      setTimeout(() => {
        restartEngine(1500, currentFactoryIndex);
      }, 500);
      return;
    }
    const fallbackIndex = getEngineIndexById(FALLBACK_ENGINE_ID);
    console.log('[FALLBACK] Disconnect 405/Connection Failure detectado, alternando imediatamente para', FALLBACK_ENGINE_ID);
    setTimeout(() => {
      restartEngine(0, fallbackIndex >= 0 ? fallbackIndex : currentFactoryIndex);
    }, 500);
    return;
  }

  if (isBaileys) {
    startFallbackTimer('disconnect');
  }
  setTimeout(() => {
    restartEngine(2000);
  }, 2000);
}

function attachEngineListeners(engine) {
  engine.on('qr', handleQr);
  engine.on('ready', handleReady);
  engine.on('authenticated', handleAuthenticated);
  engine.on('auth_failure', handleAuthFailure);
  engine.on('disconnected', handleDisconnected);
  engine.on('message', onMessage);
  engine.on('outgoing_message', onOutgoingMessage);
  engine.on('typing', onTyping);
  engine.on('loading_screen', handleLoadingScreen);
  engine.on('error', (err) => {
    console.log('[ENGINE_ERROR]', String(err && err.message || err));
  });
}

const engineFactories = [
  { id: 'baileys', create: () => createBaileysEngine({ authFile: BAILEYS_AUTH_FILE }) },
  ...(ENABLE_WWEBJS_FALLBACK
    ? [{ id: 'wwebjs', create: () => createWwebJsEngine(ENGINE_OPTIONS) }]
    : [])
];

let currentFactoryIndex = 0;
let engineStartTimeout = null;

async function startEngineFromFactory(index = 0) {
  const safeIndex = index % engineFactories.length;
  currentFactoryIndex = safeIndex;
  cancelFallbackTimer();
  await destroyActiveEngine();
  const engineSetup = engineFactories[safeIndex];
  console.log('[ENGINE] Inicializando engine:', engineSetup.id, '| fallback wwebjs:', ENABLE_WWEBJS_FALLBACK ? 'enabled' : 'disabled');
  const engine = engineSetup.create();
  attachEngineListeners(engine);
  activeEngine = engine;
  const isBaileys = engineSetup.id === 'baileys';
  if (isBaileys) {
    startFallbackTimer('boot');
  }
  try {
    await engine.initialize();
  } catch (e) {
    console.log(`[ENGINE] Falha ao iniciar ${engineSetup.id}:`, String(e && e.message || e));
    throw e;
  }
}

function scheduleEngineStart(delay = 0, factoryIndex = 0) {
  if (engineStartTimeout) {
    clearTimeout(engineStartTimeout);
    engineStartTimeout = null;
  }
  engineStartTimeout = setTimeout(() => {
    startEngineFromFactory(factoryIndex).catch(err => {
      console.log('[ENGINE] Erro ao iniciar engine:', String(err && err.message || err));
    });
  }, delay);
}

async function restartEngine(delay = 0, factoryIndex = currentFactoryIndex) {
  try {
    await destroyActiveEngine();
  } catch (e) {
    console.log('[ENGINE] Erro ao destruir engine:', String(e && e.message || e));
  }
  scheduleEngineStart(delay, factoryIndex);
}

function getEngineIndexById(id) {
  return engineFactories.findIndex((factory) => factory.id === id);
}

function startFallbackTimer(reason = 'timeout') {
  cancelFallbackTimer();
  if (!ENABLE_WWEBJS_FALLBACK) return;
  if (!activeEngine || activeEngine.getId() !== 'baileys') return;
  const fallbackIndex = getEngineIndexById(FALLBACK_ENGINE_ID);
  if (fallbackIndex < 0) return;
  fallbackTimer = setTimeout(() => {
    if (activeEngine && activeEngine.getId() === 'baileys') {
      console.log(`[FALLBACK] ${reason}: Baileys nÃ£o respondeu em 4 minutos, ativando ${FALLBACK_ENGINE_ID}`);
      restartEngine(0, fallbackIndex);
    }
  }, FALLBACK_DELAY_MS);
}

function cancelFallbackTimer() {
  if (fallbackTimer) {
    clearTimeout(fallbackTimer);
    fallbackTimer = null;
  }
}

setInterval(() => {
  checkExternalResetRequest().catch(() => {});
}, 5000);

process.on('uncaughtException', (e) => {
  console.log('UNCAUGHT', String(e && e.message || e));
  restartEngine(2000);
});

process.on('unhandledRejection', (e) => {
  console.log('UNHANDLED_REJECTION', String(e && e.message || e));
  restartEngine(2000);
});

console.log('INITIALIZING ENGINE...');
console.log('[ENGINE] Strategy: primary=baileys | fallback=wwebjs', ENABLE_WWEBJS_FALLBACK ? '(enabled)' : '(disabled)');
startEngineFromFactory(currentFactoryIndex)
  .then(() => console.log('INITIALIZE CALL DONE'))
  .catch(e => console.error('INIT ERROR', e));
console.log('WAITING FOR EVENTS...');
