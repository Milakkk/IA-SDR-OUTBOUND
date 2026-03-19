/**
 * Script para enviar mensagem para todos os números da whitelist
 */
const path = require('path');
const fs = require('fs');
const dotenv = require('dotenv');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

// Carrega .env da raiz
const rootEnvPath = path.resolve(__dirname, '..', '.env');
if (fs.existsSync(rootEnvPath)) {
  dotenv.config({ path: rootEnvPath });
  console.log('[ENV] Carregando .env da raiz:', rootEnvPath);
} else {
  dotenv.config();
}

// Carrega config.json unificado (prioridade sobre .env)
const configPath = path.resolve(__dirname, '..', 'config.json');
let CONFIG = {};
if (fs.existsSync(configPath)) {
  try {
    CONFIG = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    console.log('[CONFIG] ✅ Carregando config.json unificado');
  } catch (e) {
    console.log('[CONFIG] ⚠️ Erro ao carregar config.json:', e.message);
  }
}

// Números - usa config.json ou .env
const ALLOWED_CONTACTS_RAW = CONFIG.allowed_contacts || process.env.ALLOWED_CONTACTS || '';
const ALLOWED_CONTACTS = Array.isArray(ALLOWED_CONTACTS_RAW) 
  ? ALLOWED_CONTACTS_RAW.map(n => String(n).replace(/\D/g, '')).filter(Boolean)
  : String(ALLOWED_CONTACTS_RAW)
      .split(',')
      .map(s => s.trim())
      .map(s => s.replace(/\D/g, ''))
      .filter(Boolean);

// Mensagem melhorada
const MESSAGE = `Boa tarde! 👋

Me chamo Eduardo, sou o SDR da Silicon. O Milak me pediu para entrar em contato com você para fazer alguns testes e verificar se estou funcionando direitinho.

Quando puder, é só iniciar a conversa aqui que eu respondo! 😊`;

console.log('='.repeat(80));
console.log('📤 ENVIO DE MENSAGEM PARA WHITELIST');
console.log('='.repeat(80));
console.log(`\n📋 Números na whitelist: ${ALLOWED_CONTACTS.length}`);
ALLOWED_CONTACTS.forEach((num, idx) => {
  console.log(`   ${idx + 1}. ${num}`);
});
console.log(`\n💬 Mensagem a ser enviada:\n${MESSAGE}\n`);
console.log('='.repeat(80));

const sessionPath = path.resolve(process.cwd(), '.wwebjs_auth');

// Tenta encontrar o Chrome no Windows
function findChromePath() {
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
      '--disable-gpu',
      '--disable-software-rasterizer',
      '--disable-extensions',
      '--disable-background-timer-throttling',
      '--disable-backgrounding-occluded-windows',
      '--disable-renderer-backgrounding'
    ],
    ignoreDefaultArgs: ['--disable-extensions']
  }
});

client.on('qr', qr => {
  console.log('\n📱 Escaneie o QR Code abaixo para conectar:\n');
  qrcode.generate(qr, { small: true });
  console.log('\n');
});

client.on('ready', async () => {
  console.log('\n✅ WhatsApp conectado! Iniciando envio de mensagens...\n');
  
  let successCount = 0;
  let errorCount = 0;
  
  for (let i = 0; i < ALLOWED_CONTACTS.length; i++) {
    const number = ALLOWED_CONTACTS[i];
    // Formata número para o formato do WhatsApp (número@c.us)
    const formattedNumber = `${number}@c.us`;
    
    try {
      console.log(`[${i + 1}/${ALLOWED_CONTACTS.length}] Enviando para ${number}...`);
      
      // Verifica se o chat existe
      const chat = await client.getChatById(formattedNumber);
      
      // Envia mensagem
      await client.sendMessage(formattedNumber, MESSAGE);
      
      console.log(`   ✅ Mensagem enviada com sucesso para ${number}`);
      successCount++;
      
      // Aguarda 2 segundos entre mensagens para evitar spam
      if (i < ALLOWED_CONTACTS.length - 1) {
        await new Promise(r => setTimeout(r, 2000));
      }
    } catch (error) {
      console.log(`   ❌ Erro ao enviar para ${number}: ${error.message}`);
      errorCount++;
    }
  }
  
  console.log('\n' + '='.repeat(80));
  console.log('📊 RESUMO DO ENVIO');
  console.log('='.repeat(80));
  console.log(`✅ Sucesso: ${successCount}`);
  console.log(`❌ Erros: ${errorCount}`);
  console.log(`📋 Total: ${ALLOWED_CONTACTS.length}`);
  console.log('='.repeat(80));
  
  // Fecha o cliente após 5 segundos
  setTimeout(() => {
    console.log('\n🔌 Encerrando conexão...');
    client.destroy().then(() => {
      console.log('✅ Conexão encerrada. Script finalizado.\n');
      process.exit(0);
    }).catch(() => {
      process.exit(0);
    });
  }, 5000);
});

client.on('authenticated', () => {
  console.log('✅ Autenticado!');
});

client.on('auth_failure', msg => {
  console.error('❌ Falha na autenticação:', msg);
  process.exit(1);
});

client.on('disconnected', (reason) => {
  console.log('⚠️ Desconectado:', reason);
});

console.log('\n🚀 Inicializando cliente WhatsApp...\n');
client.initialize();


