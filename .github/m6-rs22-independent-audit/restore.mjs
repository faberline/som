// Restore authenticated evidence only inside the disposable GitHub runner.
import fs from 'node:fs/promises';
import path from 'node:path';
import {decodeKey, openArchive, sha256, validateRelativePath} from '../../som-rs/scripts/react_output_acceptance/sealed_inputs.mjs';
let key;
try {
  if (process.env.GITHUB_ACTIONS !== 'true' || process.env.RUNNER_ENVIRONMENT !== 'github-hosted') throw Error();
  const temp = path.resolve(process.env.RUNNER_TEMP);
  const source = path.join(temp, 'rs22-sealed-audit');
  const out = path.join(temp, 'rs22-raw-audit');
  const idxRaw = await fs.readFile(path.join(source, 'evidence-index.json'));
  if (sha256(idxRaw) !== 'e8f1d9632bda224df5fbab141e2c106a327308555ac09699ece83877b943e0e8') throw Error();
  const idx = JSON.parse(idxRaw);
  if (idx.version !== 'som.m6-evidence-index.v1' || idx.status !== 'encrypted' || !Array.isArray(idx.packages)
      || idx.packages.length !== 1) throw Error();
  const ring = JSON.parse(process.env.M6_INPUTS_KEYRING);
  key = decodeKey(ring[idx.key_id]);
  await fs.mkdir(out, {mode: 0o700});
  const names = new Set();
  for (const item of idx.packages) {
    if (!/^package-\d{3}\.sealed$/.test(item.archive) || item.manifest !== item.archive.replace('.sealed', '.manifest.json')) throw Error();
    const manifestRaw = await fs.readFile(path.join(source, item.manifest));
    if (sha256(manifestRaw) !== item.manifest_sha256) throw Error();
    const manifest = JSON.parse(manifestRaw.toString('utf8'));
    if (manifest.cipher_sha256 !== item.cipher_sha256 || manifest.total_files !== item.total_files) throw Error();
    const archive = await fs.readFile(path.join(source, item.archive));
    const restored = openArchive(archive, manifest, key);
    for (const file of restored) {
      validateRelativePath(file.path);
      if (names.has(file.path)) throw Error();
      names.add(file.path);
      if (file.path !== 'summary.json' && !file.path.startsWith('run/')) continue;
      const target = path.join(out, file.path);
      await fs.mkdir(path.dirname(target), {recursive: true, mode: 0o700});
      await fs.writeFile(target, file.data, {flag: 'wx', mode: 0o600});
    }
  }
  if (!names.has('summary.json')) throw Error();
  console.log(JSON.stringify({status: 'restored', package_count: idx.packages.length, total_files: names.size}));
} catch {
  console.error('authenticated evidence restore failed');
  process.exitCode = 1;
} finally {
  if (key) key.fill(0);
}
