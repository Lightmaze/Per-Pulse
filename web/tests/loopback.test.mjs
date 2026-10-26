// Keep the dev proxy private: it forwards /api to an unauthenticated local
// World/Kernel service. A LAN-facing Vite server would bypass the CLI bind.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createServer } from 'vite';

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..');

test('developer Vite proxy binds only to loopback', async () => {
  const server = await createServer({
    root: webRoot,
    configFile: join(webRoot, 'vite.config.ts'),
    logLevel: 'silent',
    server: { port: 0 },
  });
  try {
    await server.listen();
    const address = server.httpServer?.address();
    assert.ok(address && typeof address !== 'string');
    assert.equal(address.address, '127.0.0.1');
    assert.equal(server.config.server.proxy['/api'], 'http://127.0.0.1:8000');
    assert.equal(server.config.preview.host, '127.0.0.1');
  } finally {
    await server.close();
  }
});

test('npm dev and preview scripts cannot override loopback config', async () => {
  const packageJson = JSON.parse(await readFile(join(webRoot, 'package.json'), 'utf8'));
  assert.equal(packageJson.scripts.dev, 'vite --host 127.0.0.1');
  assert.equal(packageJson.scripts.preview, 'vite preview --host 127.0.0.1');
});
