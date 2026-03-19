let baileys = null;
let baileysPackageName = '@adiwajshing/baileys';
try {
  baileys = require('@whiskeysockets/baileys');
  baileysPackageName = '@whiskeysockets/baileys';
} catch {
  baileys = require('@adiwajshing/baileys');
  baileysPackageName = '@adiwajshing/baileys';
}
const {
  useMultiFileAuthState,
  downloadMediaMessage,
  fetchLatestBaileysVersion,
  DEFAULT_CONNECTION_CONFIG
} = baileys;
const makeWASocket = baileys.default || baileys.makeWASocket;
const WhatsAppEngine = require('./whatsapp-engine');
const path = require('path');
const fs = require('fs');
let BAILEYS_PKG_VERSION = 'unknown';
try {
  BAILEYS_PKG_VERSION = require(`${baileysPackageName}/package.json`).version || 'unknown';
} catch {}

const ensureDir = (dir) => {
  try {
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
  } catch {}
};

const makeNoopLogger = () => {
  const noop = () => {};
  const logger = {
    child: () => logger,
    trace: noop,
    debug: noop,
    info: noop,
    warn: noop,
    error: noop,
    fatal: noop
  };
  return logger;
};

class BaileysEngine extends WhatsAppEngine {
  constructor(options = {}) {
    super();
    this.options = options;
    this.sock = null;
    this.authState = null;
    this.saveCreds = null;
    this.boundHandlers = [];
    this.authFile = options.authFile || path.resolve(process.cwd(), '.baileys_auth', 'baileys.json');
    this.authDir = options.authDir || path.dirname(this.authFile);
    this.logger = options.logger || makeNoopLogger();
  }

  getId() {
    return 'baileys';
  }

  getAuthInfo() {
    return {
      type: this.getId(),
      path: this.authDir
    };
  }

  getSelfId() {
    return this.sock?.user?.id || null;
  }

  async initialize() {
    if (this.sock) {
      await this.destroy();
    }
    console.log('[BaileysEngine] initialize -> authDir:', this.authDir, '| pkg:', baileysPackageName, BAILEYS_PKG_VERSION);
    ensureDir(this.authDir);
    const { state, saveCreds } = await useMultiFileAuthState(this.authDir);
    const defaultVersion = (DEFAULT_CONNECTION_CONFIG && DEFAULT_CONNECTION_CONFIG.version) || [2, 2243, 7];
    let version = defaultVersion;
    try {
      const latest = await fetchLatestBaileysVersion();
      version = latest.version;
      console.log(
        '[BaileysEngine] Versao WhatsApp Web alvo:',
        Array.isArray(version) ? version.join('.') : version,
        '| isLatest:',
        latest && typeof latest.isLatest === 'boolean' ? latest.isLatest : 'n/a'
      );
    } catch (error) {
      console.log('[BaileysEngine] Falha ao buscar versao do Baileys, usando fallback', error?.message);
    }
    this.authState = state;
    this.saveCreds = saveCreds;

    this.sock = makeWASocket({
      logger: this.logger,
      printQRInTerminal: false,
      version,
      auth: state,
      browser: ['Ãris Bot', 'Chrome', '1.0']
    });

    this.sock.ev.on('creds.update', saveCreds);
    this.boundHandlers.push({ event: 'creds.update', handler: saveCreds });

    this._bindSocketEvents();

    this.initialized = true;
  }

  async destroy() {
    if (!this.sock) return;
    try {
      this.boundHandlers.forEach(({ event, handler }) => {
        this.sock.ev.off(event, handler);
      });
      this.boundHandlers = [];
    } catch {}
    try {
      await this.sock.end();
    } catch (e) {
      console.log('[BaileysEngine] Erro ao encerrar sock:', String(e && e.message || e));
    }
    this.sock = null;
    this.initialized = false;
  }

  async sendMessage(chatId, message) {
    if (!this.sock) throw new Error('BaileysEngine nÃ£o inicializado');
    const jid = this._toBaileysJid(chatId);
    const res = await this.sock.sendMessage(jid, { text: message });
    return {
      raw: res,
      messageId: res?.key?.id || null,
      jid
    };
  }

  async isRegisteredUser(chatId) {
    if (!this.sock) throw new Error('BaileysEngine nao inicializado');
    const jid = this._toBaileysJid(chatId);
    const res = await this.sock.onWhatsApp(jid);
    if (!Array.isArray(res) || !res.length) return false;
    return Boolean(res[0] && res[0].exists);
  }

  async waitForOutboundAck(ref, timeoutMs = 25000) {
    if (!this.sock) throw new Error('BaileysEngine nao inicializado');
    const messageId = String(ref?.messageId || '').trim();
    if (!messageId) return false;
    const chatJid = ref?.chatId ? this._toBaileysJid(ref.chatId) : '';

    return await new Promise((resolve) => {
      let finished = false;
      const finish = (ok) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        try { this.sock.ev.off('messages.update', handler); } catch {}
        resolve(Boolean(ok));
      };

      const handler = (updates) => {
        if (!Array.isArray(updates)) return;
        for (const item of updates) {
          const key = item?.key || {};
          const incomingId = String(key.id || '').trim();
          const incomingJid = String(key.remoteJid || '').trim();
          if (!incomingId || incomingId !== messageId) continue;
          if (chatJid && incomingJid && incomingJid !== chatJid) continue;
          const status = Number(item?.update?.status ?? item?.status ?? -1);
          if (!Number.isNaN(status) && status >= 1) {
            return finish(true);
          }
        }
      };

      const timer = setTimeout(() => finish(false), Math.max(3000, Number(timeoutMs) || 25000));
      this.sock.ev.on('messages.update', handler);
    });
  }

  async getChatById(chatId) {
    return {
      sendStateTyping: async () => this.sendPresenceUpdate(chatId, 'composing'),
      clearState: async () => this.sendPresenceUpdate(chatId, 'paused')
    };
  }

  async downloadMedia(message) {
    if (!message || typeof message !== 'object') {
      throw new Error('Mensagem invÃ¡lida para download');
    }
    const buffer = await downloadMediaMessage(message, 'buffer');
    const mimetype = this._extractMediaMimetype(message);
    return { data: buffer.toString('base64'), mimetype };
  }

  async sendPresenceUpdate(chatId, status) {
    if (!this.sock) return;
    const jid = this._toBaileysJid(chatId);
    try {
      await this.sock.sendPresenceUpdate(status, jid);
    } catch {}
  }

  async applyBrowserPatch() {
    // Baileys nÃ£o usa browser
  }

  _bindSocketEvents() {
    const connectionHandler = (update) => {
      const { connection, qr, lastDisconnect } = update;
      const statusCode = lastDisconnect?.error?.output?.statusCode
        || lastDisconnect?.error?.data?.statusCode
        || null;
      const reason = lastDisconnect?.error?.data?.reason || null;
      const location = lastDisconnect?.error?.data?.location || null;
      console.log(
        '[BaileysEngine] connection.update ->',
        'connection=', connection || 'n/a',
        '| qr=', qr ? 'yes' : 'no',
        '| statusCode=', statusCode ?? 'n/a',
        '| reason=', reason ?? 'n/a',
        '| location=', location ?? 'n/a'
      );
      if (lastDisconnect) {
        console.log('[BaileysEngine] lastDisconnect', lastDisconnect);
        console.log('[BaileysEngine] lastDisconnect.error', lastDisconnect.error);
      }
      if (qr) {
        this.emit('qr', qr);
      }
      if (connection === 'open') {
        this.emit('authenticated');
        this.emit('ready');
      }
      if (connection === 'close') {
        const err = lastDisconnect ? new Error(lastDisconnect.error?.message || 'conexÃ£o fechada') : null;
        if (err && lastDisconnect) {
          err.lastDisconnect = lastDisconnect;
          err.statusCode = lastDisconnect?.error?.output?.statusCode
            || lastDisconnect?.error?.data?.statusCode
            || null;
          err.disconnectReason = lastDisconnect?.error?.data?.reason || null;
        }
        this.emit('disconnected', err);
      }
      if (connection === 'connecting') {
        this.emit('connecting');
      }
    };
    this.sock.ev.on('connection.update', connectionHandler);
    this.boundHandlers.push({ event: 'connection.update', handler: connectionHandler });

    const messagesHandler = (upsert) => {
      if (upsert.type !== 'notify') return;
      for (const msg of upsert.messages) {
        if (!msg.message) continue;
        const normalized = this._wrapMessage(msg);
        if (!normalized) continue;
        if (msg.key.fromMe) {
          this.emit('outgoing_message', normalized);
          continue;
        }
        if (normalized) {
          this.emit('message', normalized);
        }
      }
    };
    this.sock.ev.on('messages.upsert', messagesHandler);
    this.boundHandlers.push({ event: 'messages.upsert', handler: messagesHandler });

    const presenceHandler = (update) => {
      if (!update) return;
      Object.keys(update.presences || {}).forEach(jid => {
        const presence = update.presences[jid];
        if (presence && presence.composing) {
          this.emit('typing', { id: this._toWWebJid(jid) });
        }
      });
    };
    this.sock.ev.on('presence.update', presenceHandler);
    this.boundHandlers.push({ event: 'presence.update', handler: presenceHandler });

    const errorHandler = (err) => {
      this.emit('error', err);
    };
    this.sock.ev.on('error', errorHandler);
    this.boundHandlers.push({ event: 'error', handler: errorHandler });
  }

  _wrapMessage(msg) {
    const formatted = this._extractMessageContent(msg);
    if (!formatted) return null;

    const { type, body, mimetype } = formatted;
    const from = this._toWWebJid(msg.key.remoteJid);
    const self = this;
    return {
      from,
      body,
      type,
      hasMedia: type !== 'text',
      downloadMedia: async () => {
        const buffer = await downloadMediaMessage(msg, 'buffer');
        return { data: buffer.toString('base64'), mimetype };
      },
      getChat: async () => self.getChatById(from),
      raw: msg
    };
  }

  _extractMessageContent(msg) {
    if (!msg.message) return null;
    const message = msg.message.ephemeralMessage?.message || msg.message;
    const [messageType] = Object.keys(message);
    if (!messageType) return null;
    const payload = message[messageType];
    let body = '';
    let type = 'text';
    let mimetype = payload?.mimetype || '';

    switch (messageType) {
      case 'conversation':
        body = payload;
        break;
      case 'extendedTextMessage':
        body = payload.text;
        break;
      case 'imageMessage':
        body = payload.caption || '';
        type = 'image';
        break;
      case 'videoMessage':
        body = payload.caption || '';
        type = 'video';
        break;
      case 'audioMessage':
        body = payload.caption || '';
        type = payload.ptt ? 'ptt' : 'audio';
        break;
      case 'documentMessage':
        body = payload.caption || '';
        type = 'document';
        break;
      case 'stickerMessage':
        type = 'sticker';
        break;
      default:
        type = 'text';
    }

    return { type, body, mimetype };
  }

  _extractMediaMimetype(message) {
    const payload = message.message.ephemeralMessage?.message || message.message;
    const [messageType] = Object.keys(payload || {});
    if (!messageType) return '';
    return payload[messageType]?.mimetype || '';
  }

  _toBaileysJid(chatId) {
    if (!chatId) return chatId;
    if (chatId.endsWith('@c.us')) {
      return chatId.replace('@c.us', '@s.whatsapp.net');
    }
    return chatId;
  }

  _toWWebJid(jid) {
    if (!jid) return null;
    if (jid.endsWith('@s.whatsapp.net')) {
      return jid.replace('@s.whatsapp.net', '@c.us');
    }
    return jid;
  }
}

function createBaileysEngine(options) {
  return new BaileysEngine(options);
}

module.exports = { createBaileysEngine };
