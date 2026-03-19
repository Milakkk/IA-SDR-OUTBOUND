const { EventEmitter } = require('events');

class WhatsAppEngine extends EventEmitter {
  constructor() {
    super();
    this.initialized = false;
    this.destroyed = false;
  }

  async initialize() {
    throw new Error('initialize() precisa ser implementado');
  }

  async destroy() {
    throw new Error('destroy() precisa ser implementado');
  }

  async sendMessage(chatId, message) {
    throw new Error('sendMessage() precisa ser implementado');
  }

  async getChatById(chatId) {
    throw new Error('getChatById() precisa ser implementado');
  }

  /** Esta funÃ§Ã£o deve enviar o estado de presenÃ§a (composing/paused) */
  async sendPresenceUpdate(chatId, status) {
    throw new Error('sendPresenceUpdate() precisa ser implementado');
  }

  async downloadMedia(message) {
    throw new Error('downloadMedia() precisa ser implementado');
  }

  /** Fornece uma etiqueta identificadora do motor (ex: 'baileys', 'wweb') */
  getId() {
    return 'generic';
  }

  /** Data que o engine utiliza para persistÃªncia (caminho de auth, QR etc) */
  getAuthInfo() {
    return null;
  }

  getSelfId() {
    return null;
  }

  async applyBrowserPatch() {
    // Engines com Puppeteer podem sobrescrever este mÃ©todo
    return;
  }

  async waitForOutboundAck(_ref, _timeoutMs = 0) {
    return true;
  }
}

module.exports = WhatsAppEngine;

