'use strict';
// DRAFT: construct a new public React npm cache; this is not acceptance.
const fs = require('node:fs/promises');
const crypto = require('node:crypto');
const path = require('node:path');
const LOCK_SHA = 'c6f3856e1a620d78d4357ef54f89488612a5c1606bbf0219265fc984334b5814';
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const need = (ok, reason) => { if (!ok) throw new Error(reason); };
function validateBinding(row, entry) {
  need(row && entry && row.url === entry.resolved && row.version === entry.version && row.integrity === entry.integrity,
       'lock binding differs');
  const url = new URL(row.url);
  need(url.protocol === 'https:' && url.host === 'registry.npmjs.org' && !url.username && !url.password
       && !url.search && !url.hash, 'registry URL refused');
  need(/^tarballs\/[0-9a-f]{64}\.tgz$/.test(row.file), 'tarball path refused');
}
function validateBytes(row, raw) {
  need(Buffer.isBuffer(raw) && sha(raw) === row.sha256 && raw.length === row.bytes
       && `sha512-${crypto.createHash('sha512').update(raw).digest('base64')}` === row.integrity,
       'tarball bytes differ');
}

async function main() {
  need(process.env.GITHUB_ACTIONS === 'true' && process.env.RUNNER_ENVIRONMENT === 'github-hosted', 'host refused');
  const repo = path.resolve(process.env.GITHUB_WORKSPACE);
  const temp = path.resolve(process.env.RUNNER_TEMP);
  const root = path.join(temp, 'som-react-linux-qa-closure');
  const cache = path.join(root, 'npm-cache-new');
  const lockPath = path.join(repo, 'som-rs/contracts/react-output-v1/reference/package-lock.json');
  const manifestPath = path.join(root, 'public-inputs.json');
  const npmRoot = path.join(root, 'node/lib/node_modules/npm');
  const lockRaw = await fs.readFile(lockPath);
  need(sha(lockRaw) === LOCK_SHA, 'lock differs');
  const lock = JSON.parse(lockRaw.toString('utf8'));
  const manifestRaw = await fs.readFile(manifestPath);
  const manifest = JSON.parse(manifestRaw.toString('utf8'));
  need(manifest.version === 'som.react-linux-qa-public-inputs.v1' && manifest.product_acceptance_passed === false
       && manifest.source_commit === process.env.GITHUB_SHA && Array.isArray(manifest.packages)
       && manifest.packages.length === 123 && lock.lockfileVersion === 3, 'manifest differs');
  const expected = Object.entries(lock.packages).filter(([key]) => key !== '');
  need(expected.length === 123, 'lock count differs');
  const rows = new Map(manifest.packages.map(row => [row.path, row]));
  need(rows.size === expected.length, 'manifest paths repeated');
  await fs.mkdir(cache, {mode: 0o700});
  const cacache = require(path.join(npmRoot, 'node_modules/cacache'));
  const installed = [];
  for (const [key, entry] of expected) {
    const row = rows.get(key);
    validateBinding(row, entry);
    const tarball = path.join(root, row.file);
    need(!(await fs.lstat(tarball)).isSymbolicLink(), 'tarball symlink refused');
    const raw = await fs.readFile(tarball);
    validateBytes(row, raw);
    const cacheKey = `make-fetch-happen:request-cache:${row.url}`;
    const integrity = await cacache.put(path.join(cache, '_cacache'), cacheKey, raw, {
      integrity: row.integrity,
      metadata: {time: Date.now(), url: row.url, reqHeaders: {}, resHeaders: {}, options: {compress: true}},
    });
    need(String(integrity) === row.integrity, 'cache integrity differs');
    installed.push({path: key, url: row.url, integrity: row.integrity, sha256: row.sha256});
  }
  need(sha(await fs.readFile(manifestPath)) === sha(manifestRaw) && sha(await fs.readFile(lockPath)) === LOCK_SHA,
       'input changed during cache construction');
  const report = {version: 'som.react-linux-qa-cache-seed.v1', status: 'unsealed-experiment',
                  product_acceptance_passed: false, manifest_sha256: sha(manifestRaw),
                  lock_sha256: LOCK_SHA, cache_role: 'new public npm cache, not the frozen macOS cache', entries: installed};
  await fs.writeFile(path.join(root, 'seed-report.json'), JSON.stringify(report, null, 2) + '\n', {flag: 'wx'});
  console.log(JSON.stringify({state: 'cache-seeded', entries: installed.length, product_acceptance_passed: false}));
}
module.exports = {validateBinding, validateBytes};
if (require.main === module) {
  main().catch(error => {
    console.error(JSON.stringify({state: 'failed', failure_type: error.name, product_acceptance_passed: false}));
    process.exitCode = 1;
  });
}
