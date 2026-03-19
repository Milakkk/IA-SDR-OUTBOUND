/**
 * Script para enviar mensagem outbound para empresas da Receita Federal
 * Lê contatos processados do CSV e envia mensagem inicial sobre a Silicon
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

// Carrega contatos processados
const contactsFile = path.resolve(__dirname, '..', 'outbound_data', 'contacts_processed.json');
if (!fs.existsSync(contactsFile)) {
  console.error('❌ Arquivo de contatos não encontrado:', contactsFile);
  console.error('   Execute primeiro: python scripts/process_csv_outbound.py');
  process.exit(1);
}

let contactsData = {};
try {
  contactsData = JSON.parse(fs.readFileSync(contactsFile, 'utf8'));
} catch (e) {
  console.error('❌ Erro ao ler arquivo de contatos:', e.message);
  process.exit(1);
}

const contacts = contactsData.contacts || [];
if (contacts.length === 0) {
  console.error('❌ Nenhum contato encontrado no arquivo');
  process.exit(1);
}

// Carrega histórico de mensagens enviadas
const sentMessagesFile = path.resolve(__dirname, '..', 'outbound_data', 'sent_messages.json');
let sentMessages = {};
if (fs.existsSync(sentMessagesFile)) {
  try {
    sentMessages = JSON.parse(fs.readFileSync(sentMessagesFile, 'utf8'));
  } catch (e) {
    sentMessages = {};
  }
}

// Mensagem inicial personalizada sobre a Silicon
const MESSAGE = `Olá! 👋

Sou o Eduardo, da Silicon. Entramos em contato porque identificamos que sua empresa pode se beneficiar das nossas soluções em iluminação LED profissional e energia solar.

A Silicon é especialista em:
• Iluminação LED para grandes projetos (indústrias, estádios, iluminação pública)
• Usinas solares fotovoltaicas de grande porte
• Iluminação para cultivo (estufas, viveiros, casas de vegetação)
• Geração distribuída (GD)

Gostaria de conhecer melhor nossas soluções? Posso agendar uma reunião com nosso consultor Eduardo Sauaf para apresentar como podemos ajudar sua empresa.

Quando seria um bom momento para conversarmos?`;

console.log('='.repeat(80));
console.log('📤 ENVIO DE MENSAGEM OUTBOUND - RECEITA FEDERAL');
console.log('='.repeat(80));
console.log(`\n📋 Total de contatos: ${contacts.length}`);
console.log(`💬 Mensagem a ser enviada:\n${MESSAGE}\n`);
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
  let skippedCount = 0;
  
  // Filtra contatos que ainda não receberam mensagem
  const contactsToSend = contacts.filter(contact => {
    const phone = contact.phone;
    return !sentMessages[phone] || sentMessages[phone].status !== 'success';
  });
  
  console.log(`📋 Contatos para enviar: ${contactsToSend.length}`);
  console.log(`⏭️  Contatos já enviados (pulados): ${contacts.length - contactsToSend.length}\n`);
  
  for (let i = 0; i < contactsToSend.length; i++) {
    const contact = contactsToSend[i];
    const phone = contact.phone;
    const formattedNumber = `${phone}@c.us`;
    
    // Verifica se já foi enviado com sucesso
    if (sentMessages[phone] && sentMessages[phone].status === 'success') {
      skippedCount++;
      continue;
    }
    
    try {
      const empresa = contact.razao_social || contact.nome_fantasia || 'Empresa';
      console.log(`[${i + 1}/${contactsToSend.length}] 📤 Enviando para ${empresa} (${phone})...`);
      
      // Verifica se o chat existe
      const chat = await client.getChatById(formattedNumber);
      
      // Envia mensagem
      await client.sendMessage(formattedNumber, MESSAGE);
      
      // Registra sucesso
      sentMessages[phone] = {
        status: 'success',
        sent_at: new Date().toISOString(),
        contact: {
          empresa: empresa,
          cnpj: contact.cnpj,
          uf: contact.uf,
          municipio: contact.municipio
        }
      };
      
      // Salva progresso a cada 10 mensagens
      if ((i + 1) % 10 === 0) {
        fs.writeFileSync(sentMessagesFile, JSON.stringify(sentMessages, null, 2), 'utf8');
      }
      
      console.log(`   ✅ Mensagem enviada com sucesso`);
      successCount++;
      
      // Aguarda entre mensagens para evitar bloqueios
      // Delay progressivo: 3-5 segundos entre mensagens
      const delay = 3000 + Math.random() * 2000;
      if (i < contactsToSend.length - 1) {
        await new Promise(r => setTimeout(r, delay));
      }
    } catch (error) {
      console.log(`   ❌ Erro: ${error.message}`);
      
      // Registra erro
      sentMessages[phone] = {
        status: 'error',
        error: error.message,
        sent_at: new Date().toISOString(),
        contact: {
          empresa: contact.razao_social || contact.nome_fantasia || 'Empresa',
          cnpj: contact.cnpj
        }
      };
      
      errorCount++;
      
      // Em caso de erro, aguarda mais tempo antes de continuar
      await new Promise(r => setTimeout(r, 5000));
    }
  }
  
  // Salva histórico final
  fs.writeFileSync(sentMessagesFile, JSON.stringify(sentMessages, null, 2), 'utf8');
  
  console.log('\n' + '='.repeat(80));
  console.log('📊 RESUMO DO ENVIO');
  console.log('='.repeat(80));
  console.log(`✅ Sucesso: ${successCount}`);
  console.log(`❌ Erros: ${errorCount}`);
  console.log(`⏭️  Pulados (já enviados): ${skippedCount}`);
  console.log(`📋 Total processado: ${contactsToSend.length}`);
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


