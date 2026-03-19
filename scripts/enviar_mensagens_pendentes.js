/**
 * Script simplificado para enviar mensagens para contatos pendentes
 * Lê follow_ups.json e envia para contatos que ainda não receberam mensagem inicial
 */
const path = require('path');
const fs = require('fs');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

// Caminhos
const FOLLOW_UPS_FILE = path.resolve(__dirname, '..', 'outbound_data', 'follow_ups.json');

// Mensagem padrão
const DEFAULT_MESSAGE = (empresa) => 
  `Oi! Tudo bem? Aqui é a Íris, da Silicon. A gente trabalha com LED Grow (Sunna), uma iluminação que complementa a luz na estufa/viveiro com o espectro certo pra planta, ajudando a padronizar o desenvolvimento e economizar energia, com bem menos calor. Vi o nome ${empresa} e queria falar com o responsável por aí. Falo com quem?`;

// Encontra Chrome
function findChromePath() {
  try {
    const puppeteer = require('puppeteer');
    const p = puppeteer && typeof puppeteer.executablePath === 'function' ? puppeteer.executablePath() : undefined;
    if (p && fs.existsSync(p)) {
      return p;
    }
  } catch {}
  if (process.env.CHROME_PATH) {
    return process.env.CHROME_PATH;
  }
  
  const chromePaths = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe'
  ];
  
  if (process.env.LOCALAPPDATA) {
    chromePaths.push(process.env.LOCALAPPDATA + '\\Google\\Chrome\\Application\\chrome.exe');
  }
  if (process.env.PROGRAMFILES) {
    chromePaths.push(process.env.PROGRAMFILES + '\\Google\\Chrome\\Application\\chrome.exe');
  }
  if (process.env['PROGRAMFILES(X86)']) {
    chromePaths.push(process.env['PROGRAMFILES(X86)'] + '\\Google\\Chrome\\Application\\chrome.exe');
  }
  
  for (const chromePath of chromePaths) {
    try {
      if (chromePath && fs.existsSync(chromePath)) {
        return chromePath;
      }
    } catch (e) {}
  }
  
  return undefined;
}

const chromePath = findChromePath();
const sessionPath = path.resolve(process.cwd(), '.wwebjs_auth');

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: sessionPath, clientId: process.env.WPP_SESSION_ID || 'default' }),
  restartOnAuthFail: true,
  puppeteer: {
    headless: true,
    executablePath: chromePath,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu'
    ]
  }
});

client.on('qr', qr => {
  console.log('\n[QR CODE] Escaneie o QR Code abaixo:\n');
  qrcode.generate(qr, { small: true });
  console.log('\n');
});

client.on('ready', async () => {
  console.log('\n[OK] WhatsApp conectado! Verificando contatos pendentes...\n');
  
  try {
    // Carrega follow-ups
    if (!fs.existsSync(FOLLOW_UPS_FILE)) {
      console.log('[AVISO] Arquivo follow_ups.json nao encontrado');
      console.log('[INFO] Execute primeiro: python scripts/processar_contatos_base.py');
      client.destroy();
      process.exit(0);
    }
    
    let followUps = {};
    try {
      followUps = JSON.parse(fs.readFileSync(FOLLOW_UPS_FILE, 'utf8'));
    } catch (e) {
      console.log('[ERRO] Erro ao ler follow_ups.json:', e.message);
      client.destroy();
      process.exit(1);
    }
    
    // Encontra contatos que ainda não receberam mensagem inicial
    const pendingContacts = [];
    for (const phone in followUps) {
      const followUp = followUps[phone];
      // Verifica se já tem sent_at (já foi enviado)
      if (!followUp.sent_at || followUp.sent_at === '') {
        pendingContacts.push({ phone, followUp });
      }
    }
    
    if (pendingContacts.length === 0) {
      console.log('[INFO] Nenhum contato pendente para envio');
      console.log('[INFO] Todos os contatos ja receberam mensagem inicial');
      client.destroy();
      process.exit(0);
    }
    
    console.log(`[INFO] ${pendingContacts.length} contato(s) pendente(s) para envio\n`);
    
    // Envia mensagens
    let sentCount = 0;
    const now = new Date();
    
    for (const { phone, followUp } of pendingContacts) {
      const formattedNumber = `${phone}@c.us`;
      const empresa = followUp.empresa || 'Empresa';
      const message = DEFAULT_MESSAGE(empresa);
      
      try {
        console.log(`[ENVIANDO] ${empresa} (${phone})...`);
        
        await client.sendMessage(formattedNumber, message);
        
        // Marca como enviado
        followUp.sent_at = now.toISOString();
        sentCount++;
        
        console.log(`   [OK] Mensagem enviada!`);
        
        // Aguarda 3 segundos entre mensagens
        await new Promise(r => setTimeout(r, 3000));
        
      } catch (error) {
        console.log(`   [ERRO] ${error.message}`);
        // Mantém pendente para tentar novamente depois
      }
    }
    
    // Salva follow-ups atualizados
    fs.writeFileSync(FOLLOW_UPS_FILE, JSON.stringify(followUps, null, 2), 'utf8');
    
    console.log(`\n[OK] ${sentCount} mensagem(ns) enviada(s) com sucesso!`);
    
  } catch (error) {
    console.log(`[ERRO] ${error.message}`);
    process.exit(1);
  }
  
  // Fecha após 5 segundos
  setTimeout(() => {
    console.log('\n[INFO] Encerrando conexao...');
    client.destroy().then(() => {
      console.log('[OK] Script finalizado.\n');
      process.exit(0);
    }).catch(() => {
      process.exit(0);
    });
  }, 5000);
});

client.on('authenticated', () => {
  console.log('[OK] Autenticado!');
});

client.on('auth_failure', msg => {
  console.error('[ERRO] Falha na autenticacao:', msg);
  process.exit(1);
});

client.on('disconnected', (reason) => {
  console.log('[AVISO] Desconectado:', reason);
});

console.log('\n[INFO] Inicializando cliente WhatsApp...\n');
client.initialize();
