/** Actual image upload/read/source journey against a labelled local HTTP fixture.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 * The fixture verifies bytes; it does not simulate visual understanding.
 * PER_PULSE_INSTALLED_PYTHON verifies a wheel venv from a temporary working
 * directory; otherwise the journey explicitly exercises this checkout.
 */
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const installedPython = process.env.PER_PULSE_INSTALLED_PYTHON;
const python = installedPython || process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-images-'));
const screenshots = process.env.PER_PULSE_IMAGE_SCREENSHOTS;
const pythonCwd = installedPython ? directory : root;
const pythonArguments = installedPython ? ['-I'] : [];
const pythonEnv = { ...process.env };
if (installedPython) {
  delete pythonEnv.PYTHONPATH;
  delete pythonEnv.PYTHONHOME;
  pythonEnv.PYTHONNOUSERSITE = '1';
} else {
  pythonEnv.PYTHONPATH = join(root, 'src') + ':' + join(root, 'research');
}
const installation = installedPython ? JSON.parse(execFileSync(python, ['-I', '-c', `
import importlib, importlib.metadata, json, os, sys, sysconfig
from pathlib import Path
prefix = Path(sys.prefix).resolve()
site = Path(sysconfig.get_path("purelib")).resolve()
assert prefix != Path(sys.base_prefix).resolve(), "Use a fresh, isolated wheel venv"
assert site.is_relative_to(prefix) and site.name == "site-packages", "Expected this venv's site-packages"
assert "PYTHONPATH" not in os.environ and "PYTHONHOME" not in os.environ
assert sys.flags.isolated == 1
modules = {}
for name in ("per_pulse.app", "pulse_harness.adapters.image_input", "pulse_harness.adapters.chat_http",
             "pulse_system.service.kernel_runtime", "pulse_system.interaction.api.harness_kernel", "PIL"):
    location = Path(importlib.import_module(name).__file__).resolve()
    assert location.is_relative_to(site), (name, str(location), str(site))
    modules[name] = str(location)
from per_pulse.app import default_web_dir
web = default_web_dir().resolve()
assert web.is_relative_to(site) and (web / "index.html").is_file(), "Built UI must come from the installed wheel"
distribution = importlib.metadata.distribution("per-pulse")
origin = json.loads(distribution.read_text("direct_url.json") or "{}")
assert not origin.get("dir_info", {}).get("editable"), "Editable installs are not wheel acceptance"
print(json.dumps({"prefix": str(prefix), "version": distribution.version, "modules": modules, "webDir": str(web)}))
`], { cwd: pythonCwd, env: pythonEnv }).toString()) : null;
const examples = new Map(['PNG', 'JPEG'].map(format => [format, execFileSync(python, [...pythonArguments, '-c',
  'from PIL import Image; import sys; im=Image.new("RGB",(240,160),(25,100,225)); im.paste((235,50,60),(25,30,85,90)); im.save(sys.stdout.buffer,format=sys.argv[1])', format],
  { cwd: pythonCwd, env: pythonEnv })]));
const requests = [];
const seenPixels = [];
const checks = [];
const errors = [];
let browser;
let server;
let serverLog = '';

function reply(response, delta, finish) {
  response.writeHead(200, { 'Content-Type': 'text/event-stream' });
  for (const chunk of [
    { choices: [{ index: 0, delta, finish_reason: null }] },
    { choices: [{ index: 0, delta: {}, finish_reason: finish }], usage: { prompt_tokens: 80, completion_tokens: 12, total_tokens: 92 } },
  ]) response.write('data: ' + JSON.stringify(chunk) + '\n\n');
  response.end('data: [DONE]\n\n');
}
const model = createServer(async (request, response) => {
  try {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString());
    requests.push(body);
    const visuals = body.messages.flatMap(message => Array.isArray(message.content) ? message.content.filter(part => part.type === 'image_url') : []);
    if (visuals.length) {
      for (const image of visuals) {
        const bytes = Buffer.from(image.image_url.url.split(',')[1], 'base64');
        assert.ok([...examples.values()].some(expected => bytes.equals(expected)), 'Provider receives the exact original image bytes');
        seenPixels.push(bytes);
      }
      return reply(response, { content: 'Original image bytes received. [Local HTTP fixture]' }, 'stop');
    }
    const task = [...body.messages].reverse().find(message => message.role === 'user' && typeof message.content === 'string')?.content || '';
    if (!task.includes('[Attached workspace materials]')) return reply(response, { content: 'Ready. [Local HTTP fixture]' }, 'stop');
    const material = JSON.parse(task.split('[Attached workspace materials]\n').at(-1).split('\n')[0]).files[0];
    reply(response, { tool_calls: [{ index: 0, id: 'fixture-image-read', type: 'function', function: {
      name: 'read_file', arguments: JSON.stringify({ path: material.path, expected_digest: material.sha256 }),
    } }] }, 'tool_calls');
  } catch (error) {
    errors.push('HTTP fixture: ' + error.message);
    response.writeHead(500).end();
  }
});
await new Promise(done => model.listen(0, '127.0.0.1', done));
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
const env = { ...pythonEnv,
  P_PULSE_CHAT_ENDPOINT: 'http://127.0.0.1:' + model.address().port + '/chat/completions',
  P_PULSE_CHAT_MODEL: 'explicit-local-image-fixture', P_PULSE_ALLOW_LOCAL_HTTP: '1', P_PULSE_LOCAL_NO_AUTH: '1' };
delete env.P_PULSE_PROVIDER_NAMESPACE;
const start = () => {
  const child = spawn(python, [...pythonArguments, '-m', 'per_pulse.cli', '--data-dir', directory, '--port', String(port), '--no-pulse'],
    { cwd: pythonCwd, env, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', chunk => { serverLog += chunk; });
  child.stderr.on('data', chunk => { serverLog += chunk; });
  return child;
};
const stop = async child => {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 9000);
  await new Promise(done => child.once('exit', done));
  clearTimeout(force);
};
const wait = async (check, label, timeout = 20000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await check()) return; } catch { /* Observe the live service again. */ }
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const ready = () => wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
const view = () => fetch(base + '/api/pages/home').then(response => response.json());

try {
  server = start();
  await ready();
  browser = await chromium.launch({ ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'en-US', acceptDownloads: true });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.locator('.composer textarea').waitFor();
  const open = async () => {
    await page.getByRole('button', { name: 'Materials', exact: true }).click();
    await page.getByRole('dialog', { name: 'Materials', exact: true }).waitFor();
    await wait(async () => !(await page.getByRole('button', { name: 'Refresh', exact: true }).isDisabled()), 'material listing');
  };
  const close = () => page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  const shot = async name => {
    if (screenshots) { await mkdir(screenshots, { recursive: true }); await page.screenshot({ path: join(screenshots, name + '.png'), fullPage: true }); }
  };
  let lastMessage;
  for (const [format, bytes] of examples) {
    const filename = format === 'PNG' ? 'Native shapes.png' : 'Native shapes.jpg';
    await open();
    const picker = page.getByLabel('Choose a file', { exact: true });
    assert.match(await picker.getAttribute('accept'), /image\/png/);
    await picker.setInputFiles({ name: filename, mimeType: format === 'PNG' ? 'image/png' : 'image/jpeg', buffer: bytes });
    const uploaded = page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes('/materials?'));
    await page.getByRole('button', { name: 'Upload file', exact: true }).click();
    const receipt = await (await uploaded).json();
    assert.equal(receipt.material.sha256, createHash('sha256').update(bytes).digest('hex'));
    await wait(async () => page.locator('.material-image img').evaluate(image => image.complete && image.naturalWidth === 240), 'decoded preview');
    await page.getByText('When read, this image is sent to your configured model. That model must support image input.', { exact: true }).waitFor();
    await shot('image-' + format.toLowerCase() + '-desktop');
    await page.getByRole('button', { name: 'Add to draft', exact: true }).click();
    await page.locator('.composer textarea').fill('Inspect the attached source image.');
    const before = seenPixels.length;
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await wait(async () => seenPixels.length > before && (await view()).messages.at(-1)?.role === 'assistant', 'image reaches native model request');
    assert.ok(seenPixels.at(-1).equals(bytes));
    const current = await view();
    lastMessage = [...current.messages].reverse().find(message => message.materials?.some(item => item.name === filename));
    assert.ok(lastMessage);
    assert.ok(!JSON.stringify(current).includes('data:image'));
    await page.locator('[data-message-id="' + lastMessage.id + '"]').getByRole('button', { name: filename, exact: true }).click();
    await wait(async () => page.locator('.material-image img').evaluate(image => image.complete && image.naturalHeight === 160), 'source preview');
    const downloading = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download original', exact: true }).click();
    const downloaded = await downloading;
    assert.equal(downloaded.suggestedFilename(), filename);
    assert.ok((await readFile(await downloaded.path())).equals(bytes));
    await close();
    checks.push(format + ': upload, preview, native read_file, exact HTTP pixels, source and original download');
  }

  await open();
  const picker = page.getByLabel('Choose a file', { exact: true });
  await picker.setInputFiles({ name: 'broken.png', mimeType: 'image/png', buffer: Buffer.from('broken image') });
  await page.getByRole('button', { name: 'Upload file', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'This image could not be read.' }).waitFor();
  await picker.setInputFiles({ name: 'large.jpg', mimeType: 'image/jpeg', buffer: Buffer.alloc(1024 * 1024 + 1) });
  await page.getByRole('button', { name: 'Upload file', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'at most 1 MiB' }).waitFor();
  await close();
  checks.push('Broken and oversized images show actionable errors without adding a source');

  await stop(server);
  server = start();
  await ready();
  await page.reload();
  await page.locator('.composer textarea').waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('[data-message-id="' + lastMessage.id + '"]').getByRole('button', { name: 'Native shapes.jpg', exact: true }).click();
  await wait(async () => page.locator('.material-image img').evaluate(image => image.complete && image.naturalWidth === 240), 'persisted source after restart');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await shot('image-source-mobile-restart');
  await close();
  const before = seenPixels.length;
  await page.locator('.composer textarea').fill('Continue without rereading the images.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => (await view()).messages.at(-1)?.content?.includes('Ready. [Local HTTP fixture]'), 'new turn after restart');
  assert.equal(seenPixels.length, before, 'Later turns must not replay old pixel payloads');
  checks.push('Restart preserves source preview on mobile; later turns do not resend old images');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, providerRequests: requests.length, imagesReceived: seenPixels.length, errors,
    ...(installation ? { installation } : { source: 'checkout' }) }, null, 2));
} catch (error) {
  console.error(serverLog);
  throw error;
} finally {
  if (browser) await browser.close();
  await stop(server);
  await new Promise(done => model.close(done));
  await rm(directory, { recursive: true, force: true });
}
