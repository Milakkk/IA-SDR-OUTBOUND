const { Client, LocalAuth } = require('whatsapp-web.js');
const WhatsAppEngine = require('./whatsapp-engine');

class WWebJsEngine extends WhatsAppEngine {
  constructor(options = {}) {
    super();
    this.options = options;
    this.client = null;
    this.boundHandlers = [];
  }

  getId() {
    return 'wwebjs';
  }

  getAuthInfo() {
    return {
      type: this.getId(),
      sessionPath: this.options.sessionPath,
      clientId: this.options.sessionId
    };
  }

  getSelfId() {
    return this.client?.info?.wid?._serialized || null;
  }

  async initialize() {
    if (this.client) {
      await this.destroy();
    }

    const puppeteerOptions = {
      headless: this.options.headless !== false,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-software-rasterizer',
        '--disable-extensions',
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding'
      ],
      ignoreDefaultArgs: ['--disable-extensions']
    };

    if (!this.options.usePuppeteerDefault && this.options.chromePath) {
      puppeteerOptions.executablePath = this.options.chromePath;
    }

    this.client = new Client({
      authStrategy: new LocalAuth({
        dataPath: this.options.sessionPath || './.wwebjs_auth',
        clientId: this.options.sessionId || 'default'
      }),
      restartOnAuthFail: true,
      puppeteer: puppeteerOptions
    });

    this._bindClientEvents();

    await this.client.initialize();
    this.initialized = true;
  }

  async destroy() {
    if (!this.client) return;
    try {
      this.boundHandlers.forEach(({ event, handler }) => {
        this.client.off(event, handler);
      });
      this.boundHandlers = [];
    } catch (e) {
      console.log('[WWebJsEngine] Erro ao remover listeners:', String(e && e.message || e));
    }
    try {
      await this.client.destroy();
    } catch (e) {
      console.log('[WWebJsEngine] Erro ao destruir client:', String(e && e.message || e));
    }
    this.client = null;
    this.initialized = false;
  }

  async sendMessage(chatId, message) {
    if (!this.client) throw new Error('WWebJsEngine não inicializado');
    return this.client.sendMessage(chatId, message);
  }

  async getChatById(chatId) {
    if (!this.client) throw new Error('WWebJsEngine não inicializado');
    return this.client.getChatById(chatId);
  }

  async downloadMedia(message) {
    if (!message || typeof message.downloadMedia !== 'function') {
      throw new Error('Mensagem não suporta download de mídia');
    }
    return message.downloadMedia();
  }

  async sendPresenceUpdate(chatId, status) {
    if (!this.client || !this.client.sendPresenceUpdate) return;
    try {
      await this.client.sendPresenceUpdate(status, chatId);
    } catch {
      // ignore
    }
  }

  async applyBrowserPatch() {
    if (!this.client || !this.client.pupPage) return;
    try {
      await this.client.pupPage.evaluate(() => {
        if (window.WWebJS && window.WWebJS.sendSeen) {
          const originalSendSeen = window.WWebJS.sendSeen;
          window.WWebJS.sendSeen = async (chatId) => {
            try {
              return await originalSendSeen(chatId);
            } catch (e) {
              if (e.message && e.message.includes('markedUnread')) {
                return true;
              }
              throw e;
            }
          };
        }
      });
      console.log('[WWebJS] Patch sendSeen aplicado com sucesso');
    } catch (e) {
      console.log('[PATCH] Erro ao aplicar patch:', String(e && e.message || e));
    }
  }

  _bindClientEvents() {
    const binding = (event, handler) => {
      this.client.on(event, handler);
      this.boundHandlers.push({ event, handler });
    };

    binding('qr', (qr) => this.emit('qr', qr));
    binding('ready', () => this.emit('ready'));
    binding('authenticated', () => this.emit('authenticated'));
    binding('auth_failure', (msg) => this.emit('auth_failure', msg));
    binding('disconnected', (reason) => this.emit('disconnected', reason));
    binding('message', (msg) => this.emit('message', this._wrapMessage(msg)));
    binding('message_create', (msg) => {
      if (!msg || !msg.fromMe) return;
      const wrapped = this._wrapMessage(msg);
      if (wrapped) this.emit('outgoing_message', wrapped);
    });
    binding('typing', (chat) => this.emit('typing', this._wrapTyping(chat)));
    binding('loading_screen', (percent, message) => this.emit('loading_screen', percent, message));
    binding('error', (err) => this.emit('error', err));
  }

  _wrapMessage(msg) {
    return {
      from: msg.from,
      body: msg.body,
      hasMedia: Boolean(msg.hasMedia),
      type: msg.type,
      downloadMedia: () => msg.downloadMedia(),
      raw: msg
    };
  }

  _wrapTyping(chat) {
    const id = chat && chat.id && (chat.id._serialized || chat.id) ? (chat.id._serialized || chat.id.toString()) : null;
    return { id };
  }
}

function createWwebJsEngine(options) {
  return new WWebJsEngine(options);
}

module.exports = { createWwebJsEngine };
