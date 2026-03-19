/**
 * Script para verificar e enviar follow-ups agendados
 * Roda periodicamente (via cron/scheduler) para enviar mensagens de follow-up
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

// Carrega config.json
const configPath = path.resolve(__dirname, '..', 'config.json');
let CONFIG = {};
if (fs.existsSync(configPath)) {
  try {
    CONFIG = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  } catch (e) {
    console.log('[CONFIG] ⚠️ Erro ao carregar config.json:', e.message);
  }
}

const followUpsFile = path.resolve(__dirname, '..', 'outbound_data', 'follow_ups.json');
const sentMessagesFile = path.resolve(__dirname, '..', 'outbound_data', 'sent_messages.json');

// Mensagens de follow-up
const FOLLOW_UP_MESSAGES = {
  follow_up_1: `Olá! 👋

Tentei entrar em contato há alguns dias. Ainda tem interesse em conhecer as soluções da Silicon para sua empresa?

Estamos prontos para ajudar com iluminação LED profissional, energia solar e soluções para cultivo.

Posso agendar uma conversa rápida com nosso consultor Eduardo Sauaf?`,
  
  follow_up_2: `Olá! 

Última tentativa de contato. Se ainda tiver interesse nas soluções da Silicon, estou à disposição.

Caso contrário, tudo bem! Se mudar de ideia no futuro, é só me chamar. 😊`
};

const sessionPath = path.resolve(process.cwd(), '.wwebjs_auth');

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
  console.log('\n✅ WhatsApp conectado! Verificando follow-ups...\n');
  
  if (!fs.existsSync(followUpsFile)) {
    console.log('⚠️  Nenhum arquivo de follow-ups encontrado.');
    client.destroy();
    process.exit(0);
  }
  
  let followUps = {};
  try {
    followUps = JSON.parse(fs.readFileSync(followUpsFile, 'utf8'));
  } catch (e) {
    console.error('❌ Erro ao ler arquivo de follow-ups:', e.message);
    client.destroy();
    process.exit(1);
  }
  
  // Carrega histórico de respostas (do bot principal)
  let responses = {};
  const responsesFile = path.resolve(__dirname, '..', 'outbound_data', 'responses_tracking.json');
  if (fs.existsSync(responsesFile)) {
    try {
      responses = JSON.parse(fs.readFileSync(responsesFile, 'utf8'));
    } catch (e) {
      responses = {};
    }
  }
  
  const now = new Date();
  let sentCount = 0;
  let markedLostCount = 0;
  let needsUpdate = false;
  
  for (const phone in followUps) {
    const followUp = followUps[phone];
    
    // Se já respondeu, pula
    if (followUp.responded || responses[phone]) {
      if (!followUp.responded) {
        followUp.responded = true;
        followUp.responded_at = new Date().toISOString();
        needsUpdate = true;
      }
      continue;
    }
    
    // Se status não é active, pula
    if (followUp.status !== 'active') {
      continue;
    }
    
    // Verifica cada follow-up agendado
    for (let i = 0; i < followUp.follow_ups.length; i++) {
      const fu = followUp.follow_ups[i];
      
      if (fu.sent) {
        continue;
      }
      
      const scheduledDate = new Date(fu.scheduled_for);
      
      // Se já passou a data agendada
      if (now >= scheduledDate) {
        try {
          const formattedNumber = `${phone}@c.us`;
          const message = FOLLOW_UP_MESSAGES[fu.message_type] || FOLLOW_UP_MESSAGES.follow_up_1;
          
          console.log(`📤 Enviando follow-up ${i + 1} para ${followUp.empresa} (${phone})...`);
          
          await client.sendMessage(formattedNumber, message);
          
          fu.sent = true;
          fu.sent_at = now.toISOString();
          sentCount++;
          needsUpdate = true;
          
          console.log(`   ✅ Follow-up enviado`);
          
          // Aguarda 2 segundos entre mensagens
          await new Promise(r => setTimeout(r, 2000));
          
        } catch (error) {
          console.log(`   ❌ Erro ao enviar: ${error.message}`);
          fu.error = error.message;
        }
      }
    }
    
    // Verifica se todos os follow-ups foram enviados e não houve resposta
    const allFollowUpsSent = followUp.follow_ups.every(fu => fu.sent);
    const lastFollowUpDate = followUp.follow_ups.length > 0 
      ? new Date(followUp.follow_ups[followUp.follow_ups.length - 1].scheduled_for)
      : null;
    
    // Se todos foram enviados e passou mais de 7 dias do último, marca como perdido
    if (allFollowUpsSent && !followUp.responded && lastFollowUpDate) {
      const daysSinceLastFollowUp = (now - lastFollowUpDate) / (1000 * 60 * 60 * 24);
      
      if (daysSinceLastFollowUp >= 7) {
        followUp.status = 'lost';
        followUp.marked_lost_at = now.toISOString();
        markedLostCount++;
        needsUpdate = true;
        
        console.log(`   ⚠️  ${followUp.empresa} marcado como PERDIDO (sem resposta após todos os follow-ups)`);
      }
    }
  }
  
  // Salva atualizações
  if (needsUpdate) {
    fs.writeFileSync(followUpsFile, JSON.stringify(followUps, null, 2), 'utf8');
  }
  
  console.log('\n' + '='.repeat(80));
  console.log('📊 RESUMO');
  console.log('='.repeat(80));
  console.log(`✅ Follow-ups enviados: ${sentCount}`);
  console.log(`⚠️  Marcados como perdidos: ${markedLostCount}`);
  console.log('='.repeat(80));
  
  // Fecha o cliente após 3 segundos
  setTimeout(() => {
    console.log('\n🔌 Encerrando conexão...');
    client.destroy().then(() => {
      console.log('✅ Script finalizado.\n');
      process.exit(0);
    }).catch(() => {
      process.exit(0);
    });
  }, 3000);
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


