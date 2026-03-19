const fs = require('fs');
const data = JSON.parse(fs.readFileSync('outbound_data/follow_ups.json', 'utf8'));
console.log('Status atual dos contatos:');
Object.entries(data).forEach(([phone, info]) => {
  console.log(`${phone}: sent_at='${info.sent_at}' (empresa: ${info.empresa})`);
});