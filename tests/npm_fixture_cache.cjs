// Candidate synthetic-fixture preparation through the installed npm's own APIs.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
let networkAttempts = 0;
function forbidden() { networkAttempts++; throw new Error('FIXTURE_NETWORK_FORBIDDEN'); }
for (const name of ['node:http', 'node:https']) {
  const module = require(name); module.request = forbidden; module.get = forbidden;
}
require('node:net').connect = forbidden;
require('node:net').createConnection = forbidden;
require('node:tls').connect = forbidden;
global.fetch = forbidden;
const [npmCli, archive, cacheRoot, integrity, resolved, expectedName, expectedVersion] = process.argv.slice(2);
const modules = path.join(path.dirname(npmCli), '..', 'node_modules');
const pacote = require(path.join(modules, 'pacote'));
const cacache = require(path.join(modules, 'cacache'));
const ssri = require(path.join(modules, 'ssri'));
(async () => {
  const started = performance.now();
  const data = fs.readFileSync(archive);
  assert.ok(data.length > 0 && data.length < 1048576);
  assert.ok(ssri.checkData(data, integrity));
  const cache = path.join(cacheRoot, '_cacache');
  const options = { cache, integrity, offline: true, ignoreScripts: true, preferOnline: false, _isRoot: true };
  const fetcher = new pacote.FileFetcher(archive, options);
  const key = `pacote:tarball:${fetcher.from}`;
  assert.equal(await cacache.get.info(cache, key), null, 'fixture cache must start fresh');
  // This is the same tarball-stream API used by npm cache add.
  await pacote.tarball.stream(archive, stream => {
    stream.resume(); return stream.promise();
  }, options);
  const manifest = await pacote.manifest(archive, { ...options, fullMetadata: true });
  assert.equal(manifest.name, expectedName); assert.equal(manifest.version, expectedVersion);
  const info = await cacache.get.info(cache, key);
  assert.ok(info); assert.equal(info.key, key); assert.equal(info.integrity, integrity);
  const fromIndex = await cacache.get(cache, key, { memoize: false });
  const fromDigest = await cacache.get.byDigest(cache, integrity, { memoize: false });
  assert.ok(fromIndex.data.equals(data)); assert.ok(fromDigest.equals(data));
  // Lock-resolved remote retrieval must hit the same exact body by its SRI.
  const locked = await pacote.tarball(resolved, options);
  assert.ok(locked.equals(data)); assert.equal(networkAttempts, 0);
  console.log(JSON.stringify({ status: 'STOCK_NPM_FIXTURE_CACHE_VERIFIED',
    archiveSHA256: crypto.createHash('sha256').update(data).digest('hex'), integrity,
    indexKeySHA256: crypto.createHash('sha256').update(key).digest('hex'),
    indexVerified: true, digestLookupVerified: true, lockResolvedBodyVerified: true,
    networkAttempts, name: manifest.name, version: manifest.version,
    secondsInsideAPI: (performance.now()-started)/1000 }));
})().catch(error => { console.error(error.code || error.message); process.exitCode = 1; });