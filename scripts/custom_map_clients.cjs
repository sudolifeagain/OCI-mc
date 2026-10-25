// ローカル検証専用。公開サーバー・Microsoftアカウントへ接続しない。
const root = '/opt/minecraft/custom-maps/qa/client/node_modules/';
const protocol = require(root + 'minecraft-protocol');
const [version, port] = process.argv.slice(2);
const joined = new Set();
const errors = [];
const clients = [];
for (let i = 1; i <= 4; i++) {
  const name = 'OCI_QA' + i;
  const client = protocol.createClient({host: '127.0.0.1', port: Number(port), version, username: name, auth: 'offline', hideErrors: true});
  clients.push(client);
  client.on('login', () => joined.add(name));
  client.on('packet', (data, meta) => {
    if (meta.name === 'position' && data.teleportId !== undefined) {
      client.write('teleport_confirm', {teleportId: data.teleportId});
    }
  });
  client.on('error', error => errors.push(name + ': ' + error.message));
  client.on('kick_disconnect', reason => errors.push(name + ': kick ' + JSON.stringify(reason)));
}
setTimeout(() => {
  console.log(JSON.stringify({joined: [...joined], errors}));
  clients.forEach(c => c.end());
  setTimeout(() => process.exit(joined.size === 4 ? 0 : 1), 500);
}, 20000);
