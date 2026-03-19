/**
 * Script que envia mensagens para contatos da fila (follow_ups.json)
 * Pode ser executado periodicamente ou manualmente
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
} else {
  dotenv.config();
}

// Caminhos
const FOLLOW_UPS_FILE = path.resolve(__dirname, '..', 'outbound_data', 'follow_ups.json');
const SENT_QUEUE_FILE = path.resolve(__dirname, '..', 'outbound_data', 'send_queue.json');

// Mensagem padrão
const DEFAULT_MESSAGE = () =>
  `Ola, tudo bem?\n\nSou da Silicon e trabalhamos com iluminacao para o aumento de fotossintese de mudas.\n\nVi que voces trabalham com producao de mudas e preciso falar com o responsavel pelo manejo do viveiro.\n\nConsegue me ajudar a falar com o responsavel?`;

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
  console.log('\n✅ WhatsApp conectado! Verificando fila de envio...\n');
  
  try {
    // Carrega follow-ups
    let followUps = {};
    if (fs.existsSync(FOLLOW_UPS_FILE)) {
      try {
        followUps = JSON.parse(fs.readFileSync(FOLLOW_UPS_FILE, 'utf8'));
      } catch (e) {
        console.log('   ⚠️  Erro ao ler follow_ups.json');
        process.exit(1);
      }
    }
    
    // Carrega fila de envio (contatos que precisam receber mensagem inicial)
    let sendQueue = [];
    if (fs.existsSync(SENT_QUEUE_FILE)) {
      try {
        sendQueue = JSON.parse(fs.readFileSync(SENT_QUEUE_FILE, 'utf8'));
      } catch (e) {
        sendQueue = [];
      }
    }
    
    // Processa contatos que ainda não receberam mensagem inicial
    let sentCount = 0;
    const now = new Date();
    
    for (const phone in followUps) {
      const followUp = followUps[phone];
      
      // Verifica se já foi enviado (tem sent_at)
      if (followUp.sent_at && followUp.sent_at !== '') {
        continue; // Já foi enviado
      }
      
      // Verifica se está na fila
      if (sendQueue.includes(phone)) {
        continue; // Já está na fila
      }
      
      // Adiciona à fila
      sendQueue.push(phone);
    }
    
    if (sendQueue.length === 0) {
      console.log('   ℹ️  Nenhum contato pendente para envio');
      client.destroy();
      process.exit(0);
    }
    
    console.log(`   📤 ${sendQueue.length} contato(s) na fila para envio\n`);
    
    // Envia mensagens
    for (const phone of sendQueue) {
      const followUp = followUps[phone];
      if (!followUp) continue;
      
      const formattedNumber = `${phone}@c.us`;
      const empresa = followUp.empresa || 'Empresa';
      const message = DEFAULT_MESSAGE(empresa);
      
      try {
        console.log(`📤 Enviando para ${empresa} (${phone})...`);
        
        await client.sendMessage(formattedNumber, message);
        
        // Marca como enviado
        followUp.sent_at = now.toISOString();
        sentCount++;
        
        console.log(`   ✅ Mensagem enviada!`);
        
        // Remove da fila
        sendQueue = sendQueue.filter(p => p !== phone);
        
        // Aguarda 3 segundos entre mensagens
        await new Promise(r => setTimeout(r, 3000));
        
      } catch (error) {
        console.log(`   ❌ Erro: ${error.message}`);
        // Mantém na fila para tentar novamente depois
      }
    }
    
    // Salva follow-ups atualizados
    fs.writeFileSync(FOLLOW_UPS_FILE, JSON.stringify(followUps, null, 2), 'utf8');
    
    // Salva fila atualizada
    fs.writeFileSync(SENT_QUEUE_FILE, JSON.stringify(sendQueue, null, 2), 'utf8');
    
    console.log(`\n✅ ${sentCount} mensagem(ns) enviada(s) com sucesso!`);
    
  } catch (error) {
    console.log(`❌ Erro: ${error.message}`);
    process.exit(1);
  }
  
  // Fecha após 5 segundos
  setTimeout(() => {
    console.log('\n🔌 Encerrando conexão...');
    client.destroy().then(() => {
      console.log('✅ Script finalizado.\n');
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
