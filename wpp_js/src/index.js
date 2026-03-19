import qrcode from 'qrcode-terminal'
import pkg from 'whatsapp-web.js'
const { Client, LocalAuth } = pkg
import { spawn } from 'child_process'
const allowed = (process.env.ALLOWED_CONTACTS || '').split(',').map(s => s.trim()).filter(Boolean)
const steps = [
  'Olá, tudo bem? Aqui é o assistente da Silicon. Você possui estrutura de casa de vegetação ou estufa?',
  'Quais culturas dentro da matriz de testes? Eucalipto, Pinus, Café, Cana, Microverde, Hortaliças.',
  'Precisa ser muda (sem cultivo)?'
]
const state = new Map()
function jidFromPhone(p){
  return `${p}@c.us`
}
function normalizeYesNo(text){
  const s = (text||'').toLowerCase()
  if (s.includes('sim')||s.includes('tenho')||s.includes('possuo')||s.includes('tem')||s.includes('existe')) return true
  if (s.includes('nao')||s.includes('não')||s.includes('não tenho')||s.includes('nao tenho')||s.includes('não possuo')||s.includes('nao possuo')) return false
  return null
}
const CULTURES = ['eucalipto','pinus','café','cafe','cana','microverde','hortaliças','hortalicas']
function parseCultures(text){
  const s = (text||'').toLowerCase()
  const found = []
  for(const c of CULTURES){
    if(s.includes(c)){
      found.push(c==='hortalicas'?'hortaliças':(c==='cafe'?'café':c))
    }
  }
  return [...new Set(found)]
}
function parseSeedling(text){
  const s = (text||'').toLowerCase()
  if (s.includes('muda')||s.includes('sem cultivo')) return true
  if (s.includes('não precisa')||s.includes('nao precisa')||s.includes('adulto')||s.includes('cultivo')) return false
  return null
}
function callPythonActions(summary){
  return new Promise((resolve)=>{
    const payload = JSON.stringify(summary)
    const py = spawn('python', ['scripts/conversation_actions.py', payload], { cwd: process.cwd() })
    let out = ''
    let err = ''
    py.stdout.on('data',d=>{ out += d.toString() })
    py.stderr.on('data',d=>{ err += d.toString() })
    py.on('close',code=>{
      resolve({ code, out, err })
    })
  })
}
const client = new Client({
  authStrategy: new LocalAuth({ clientId: 'grow', dataPath: './.wwebjs_auth' }),
  puppeteer: { headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox'] }
})
client.on('qr', qr => { qrcode.generate(qr, { small: true }) })
client.on('ready', async () => {
  for(const phone of allowed){
    const chatId = jidFromPhone(phone)
    state.set(chatId, { step:0, answers:{} })
    await client.sendMessage(chatId, steps[0])
  }
})
client.on('message', async msg => {
  const chatId = msg.from
  if (!state.has(chatId)) return
  const st = state.get(chatId)
  if (st.step===0){
    st.answers.estrutura = normalizeYesNo(msg.body)
    st.step = 1
    await client.sendMessage(chatId, steps[1])
  } else if (st.step===1){
    st.answers.cultivos = parseCultures(msg.body)
    st.step = 2
    await client.sendMessage(chatId, steps[2])
  } else if (st.step===2){
    st.answers.muda = parseSeedling(msg.body)
    const phone = chatId.replace('@c.us','')
    const summary = { phone, estrutura: st.answers.estrutura, cultivos: st.answers.cultivos, muda: st.answers.muda }
    await client.sendMessage(chatId, 'Obrigado! Vou organizar uma reunião e te confirmo por aqui.')
    const res = await callPythonActions(summary)
    state.delete(chatId)
    console.log(res.out || '')
  }
})
client.initialize()