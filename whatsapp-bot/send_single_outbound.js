/**
 * Script para enviar mensagem outbound inicial para um contato específico
 * e inicializar o sistema de tracking
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
}

// Configuração do contato
const TARGET_PHONE = process.env.TARGET_PHONE || '5511999999999';
const TARGET_EMPRESA = process.env.TARGET_EMPRESA || 'Empresa Exemplo';
const TARGET_CONTATO = process.env.TARGET_CONTATO || 'Contato Exemplo';
const TARGET_CARGO = process.env.TARGET_CARGO || '';

// Mensagem inicial outbound
const MESSAGE = `Ola, tudo bem?\n\nSou da Silicon e trabalhamos com iluminacao para o aumento de fotossintese de mudas.\n\nVi que voces trabalham com producao de mudas e preciso falar com o responsavel pelo manejo do viveiro.\n\nConsegue me ajudar a falar com o responsavel?`;

// Caminhos
const sessionPath = path.join(__dirname, '.wwebjs_auth');
const followUpsFile = path.resolve(__dirname, '..', 'outbound_data', 'follow_ups.json');

// Função para encontrar Chrome
function findChromePath() {
  const { execSync } = require('child_process');
  const chromePaths = [
    process.env.CHROME_PATH,
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    process.env.LOCALAPPDATA + '\\Google\\Chrome\\Application\\chrome.exe',
    process.env.PROGRAMFILES + '\\Google\\Chrome\\Application\\chrome.exe',
    process.env['PROGRAMFILES(X86)'] + '\\Google\\Chrome\\Application\\chrome.exe'
  ].filter(Boolean);

  // Tenta usar o executável do Puppeteer primeiro
  try {
    const puppeteer = require('puppeteer');
    const browserFetcher = puppeteer.createBrowserFetcher();
    const revisionInfo = browserFetcher.revisionInfo();
    if (revisionInfo && revisionInfo.executablePath) {
      return revisionInfo.executablePath;
    }
  } catch (e) {
    // Ignora erro
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
  authStrategy: new LocalAuth({ 
    dataPath: sessionPath, 
    clientId: process.env.WPP_SESSION_ID || 'default' 
  }),
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
  console.log('\n✅ WhatsApp conectado! Enviando mensagem outbound...\n');
  
  try {
    // Normaliza telefone
    const phoneDigits = TARGET_PHONE.replace(/\D/g, '');
    const formattedNumber = `${phoneDigits}@c.us`;
    
    console.log(`📤 Enviando mensagem para ${TARGET_EMPRESA} (${phoneDigits})...`);
    
    // Envia mensagem
    await client.sendMessage(formattedNumber, MESSAGE);
    
    console.log('   ✅ Mensagem enviada com sucesso!');
    
    // Carrega ou cria arquivo de follow-ups
    let followUps = {};
    if (fs.existsSync(followUpsFile)) {
      try {
        followUps = JSON.parse(fs.readFileSync(followUpsFile, 'utf8'));
      } catch (e) {
        console.log('   ⚠️  Erro ao ler follow_ups.json, criando novo arquivo');
      }
    }
    
    // Cria diretório se não existir
    const followUpsDir = path.dirname(followUpsFile);
    if (!fs.existsSync(followUpsDir)) {
      fs.mkdirSync(followUpsDir, { recursive: true });
    }
    
    const now = new Date();

    followUps[phoneDigits] = {
      empresa: TARGET_EMPRESA,
      contato: TARGET_CONTATO,
      cargo: TARGET_CARGO,
      sent_at: now.toISOString(),
      responded: false,
      status: 'active'
    };
    
    // Salva follow-ups
    fs.writeFileSync(followUpsFile, JSON.stringify(followUps, null, 2), 'utf8');
    console.log('   ✅ Tracking inicial salvo sem follow-up automático');
    
    console.log('\n✅ Mensagem outbound enviada e sistema de tracking inicializado!');
    console.log('\n📊 Para atualizar CSV de tracking, execute:');
    console.log('   python scripts/track_outbound_to_csv.py');
    console.log('\n🔌 Para receber respostas, inicie o bot principal:');
    console.log('   node whatsapp-bot/index.js');
    console.log('\n⚠️  Encerrando este script em 5 segundos...\n');
    
    // Encerra após 5 segundos (mensagem já foi enviada)
    setTimeout(() => {
      console.log('✅ Script finalizado. Bot principal deve ser iniciado separadamente.\n');
      client.destroy();
      process.exit(0);
    }, 5000);
    
  } catch (error) {
    console.error('❌ Erro ao enviar mensagem:', error.message);
    client.destroy();
    process.exit(1);
  }
});

client.on('disconnected', (reason) => {
  console.log('\n⚠️  Cliente desconectado:', reason);
  console.log('   Reiniciando...\n');
  client.initialize();
});

// Tratamento de erros
process.on('uncaughtException', (error) => {
  console.error('❌ Erro não capturado:', error);
});

process.on('unhandledRejection', (reason, promise) => {
  console.error('❌ Promise rejeitada:', reason);
});

client.initialize();
