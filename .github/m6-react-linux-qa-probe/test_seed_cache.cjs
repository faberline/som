'use strict';
// Synthetic-only tests; execute only on GitHub-hosted runners.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {validateBinding, validateBytes} = require('./seed_cache.cjs');
if (process.env.GITHUB_ACTIONS !== 'true' || process.env.RUNNER_ENVIRONMENT !== 'github-hosted') {
  throw new Error('GitHub hosted runner required');
}
const raw = Buffer.from('synthetic public package');
const hash = crypto.createHash('sha256').update(raw).digest('hex');
const integrity = `sha512-${crypto.createHash('sha512').update(raw).digest('base64')}`;
const row = {url: 'https://registry.npmjs.org/example/-/example-1.0.0.tgz', version: '1.0.0',
             integrity, sha256: hash, bytes: raw.length, file: `tarballs/${hash}.tgz`};
const entry = {resolved: row.url, version: row.version, integrity};

test('exact lock binding and bytes pass', () => {
  validateBinding(row, entry);
  validateBytes(row, raw);
});
test('tampered source URL, integrity, version or path fail', () => {
  for (const bad of [{...row, url: 'https://registry.npmjs.org.evil/x'}, {...row, version: '2.0.0'},
                     {...row, integrity: 'sha512-bad'}, {...row, file: '../secret'},
                     {...row, url: 'https://user@registry.npmjs.org/x'}]) {
    assert.throws(() => validateBinding(bad, entry));
  }
  assert.throws(() => validateBinding({...row, url:'https://registry.npmjs.org/x?extra=1'},
                                      {...entry, resolved:'https://registry.npmjs.org/x?extra=1'}));
});
test('tampered bytes, length and digest fail', () => {
  assert.throws(() => validateBytes(row, Buffer.from('other')));
  assert.throws(() => validateBytes({...row, bytes: raw.length + 1}, raw));
  assert.throws(() => validateBytes({...row, sha256: '0'.repeat(64)}, raw));
  assert.throws(() => validateBytes({...row, integrity: 'sha512-bad'}, raw));
});
