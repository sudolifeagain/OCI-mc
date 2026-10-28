// ローカル検証専用。公開サーバー・Microsoftアカウントへ接続しない。
const root = '/opt/minecraft/custom-maps/qa/client/node_modules/';
const protocol = require(root + 'minecraft-protocol');
const [version, port, count = '4', expectedOpLevel] = process.argv.slice(2);
const playerCount = Number(count);
if (!Number.isInteger(playerCount) || playerCount < 2 || playerCount > 4) {
  throw new Error('検証人数は2～4人で指定する');
}
const joined = new Set();
const errors = [];
const clients = [];
const permissionLevels = {};
for (let i = 1; i <= playerCount; i++) {
  const name = 'OCI_QA' + i;
  // 26.2の登録順は公式server.jarのConfigurationProtocols/GameProtocolsで照合済み。
  // 合成クライアントの接続検証にだけ26.1のcodecと26.2のprotocol番号を組み合わせる。
  const codec = version === '26.2' ? '26.1' : version;
  const client = protocol.createClient({host: '127.0.0.1', port: Number(port), version: codec, username: name, auth: 'offline', hideErrors: true});
  if (version === '26.2') {
    const write = client.write.bind(client);
    client.write = (packet, data) => write(packet, packet === 'set_protocol' ? {...data, protocolVersion: 776} : data);
  }
  clients.push(client);
  let entityId;
  client.on('login', data => { joined.add(name); entityId = data.entityId; });
  // 描画を行わない合成クライアントはパックを明示的に辞退する。
  // サーバー強制パックは検証用複製でだけ解除している。
  client.on('add_resource_pack', data => client.write('resource_pack_receive', {uuid: data.uuid, result: 1}));
  client.on('resource_pack_send', () => client.write('resource_pack_receive', {result: 1}));
  client.on('packet', (data, meta) => {
    if (meta.name === 'entity_status' && data.entityId === entityId && data.entityStatus >= 24 && data.entityStatus <= 28) {
      permissionLevels[name] = data.entityStatus - 24;
    }
    if (meta.name === 'position' && data.teleportId !== undefined) {
      client.write('teleport_confirm', {teleportId: data.teleportId});
    }
  });
  client.on('error', error => errors.push(name + ': ' + error.message));
  client.on('kick_disconnect', reason => errors.push(name + ': kick ' + JSON.stringify(reason)));
}
setTimeout(() => {
  const permissionsVerified = expectedOpLevel === undefined || Array.from({length:playerCount}, (_,i) => 'OCI_QA' + (i + 1)).every(name => permissionLevels[name] === Number(expectedOpLevel));
  console.log(JSON.stringify({joined: [...joined], expected_players: playerCount, errors, permission_levels:permissionLevels, permissions_verified:permissionsVerified, codec: version === '26.2' ? '26.1/protocol776' : version}));
  clients.forEach(c => c.end());
  setTimeout(() => process.exit(joined.size === playerCount && permissionsVerified ? 0 : 1), 500);
}, 35000);
