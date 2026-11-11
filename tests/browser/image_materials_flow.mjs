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
// A full-screen screenshot larger than one model image may carry: it is kept
// as it is, and the model receives the service's deterministic scaled copy.
const [largeScreenshot, largeModelCopy] = JSON.parse(execFileSync(python, [...pythonArguments, '-c', `
import base64, json, os, sys
from io import BytesIO
from PIL import Image
from pulse_harness.adapters.image_input import image_metadata, model_image
image = Image.frombytes("RGB", (1600, 1000), os.urandom(1600 * 1000 * 3))
output = BytesIO(); image.save(output, format="PNG"); data = output.getvalue()
view = model_image(data, image_metadata(data))
assert view.scaled and len(data) > 1024 * 1024
print(json.dumps([base64.b64encode(data).decode(), base64.b64encode(view.data).decode()]))
`], { cwd: pythonCwd, env: pythonEnv, maxBuffer: 64 * 1024 * 1024 }).toString()).map(value => Buffer.from(value, 'base64'));
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
        assert.ok([...examples.values(), largeModelCopy].some(expected => bytes.equals(expected)),
          'Provider receives the exact original, or the scaled copy of a large original');
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

  // An ordinary user can paste a screenshot into Home without opening the
  // file browser. It must remain an unsent, source-versioned draft after a
  // browser reload; only Send can carry original pixels onto the model wire.
  const pastedBytes = examples.get('PNG');
  const originalCount = seenPixels.length;
  await page.locator('.composer textarea').fill('Please inspect this pasted screenshot.');
  const pastedUpload = page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes('/materials?'));
  await page.evaluate(encoded => {
    const bytes = Uint8Array.from(atob(encoded), character => character.charCodeAt(0));
    const clipboard = new DataTransfer();
    clipboard.items.add(new File([bytes], 'Clipboard.png', { type: 'image/png' }));
    const input = document.querySelector('.composer textarea');
    const pasted = new ClipboardEvent('paste', { clipboardData: clipboard, bubbles: true, cancelable: true });
    assertPaste(input.dispatchEvent(pasted) === false);
    function assertPaste(ok) { if (!ok) throw new Error('Image paste was not consumed by the composer'); }
  }, pastedBytes.toString('base64'));
  const pastedReceipt = await (await pastedUpload).json();
  assert.equal(pastedReceipt.material.sha256, createHash('sha256').update(pastedBytes).digest('hex'));
  await page.getByRole('button', { name: 'Inspect Clipboard.png before sending' }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Please inspect this pasted screenshot.');
  assert.equal(seenPixels.length, originalCount, 'Paste must not submit to the model');
  await page.reload();
  await page.getByRole('button', { name: 'Inspect Clipboard.png before sending' }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Please inspect this pasted screenshot.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => seenPixels.length === originalCount + 1 &&
    (await view()).messages.at(-1)?.role === 'assistant', 'pasted screenshot arrives on native model wire');
  assert.ok(seenPixels.at(-1).equals(pastedBytes));
  const sentImage = [...(await view()).messages].reverse().find(message => message.materials?.some(item => item.name === 'Clipboard.png'));
  assert.ok(sentImage, 'Pasted image retains its source on the saved message');
  checks.push('Clipboard image paste: no auto-send, persisted draft after reload, original pixels and source on explicit Send');

  // A screenshot over 1 MiB is attached, not refused. The model receives a
  // scaled copy; the Materials preview says so and the original downloads.
  const largeCount = seenPixels.length;
  await page.locator('.composer textarea').fill('Please inspect this full-screen screenshot.');
  const largeUpload = page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes('/materials?'));
  await page.evaluate(encoded => {
    const bytes = Uint8Array.from(atob(encoded), character => character.charCodeAt(0));
    const clipboard = new DataTransfer();
    clipboard.items.add(new File([bytes], 'Full screen.png', { type: 'image/png' }));
    document.querySelector('.composer textarea').dispatchEvent(new ClipboardEvent('paste', {
      clipboardData: clipboard, bubbles: true, cancelable: true,
    }));
  }, largeScreenshot.toString('base64'));
  const largeReceipt = await (await largeUpload).json();
  assert.equal(largeReceipt.material.bytes, largeScreenshot.length);
  await page.getByRole('button', { name: 'Inspect Full screen.png before sending' }).click();
  await page.getByText('Shown as the 1,600 × 1,000 copy your model receives. Download the original for full detail.', { exact: true }).waitFor();
  await shot('image-large-screenshot-preview');
  const largeDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download original', exact: true }).click();
  assert.ok((await readFile(await (await largeDownload).path())).equals(largeScreenshot));
  await close();
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => seenPixels.length === largeCount + 1 &&
    (await view()).messages.at(-1)?.role === 'assistant', 'large screenshot reaches native model request');
  assert.ok(seenPixels.at(-1).equals(largeModelCopy) && largeModelCopy.length <= 1024 * 1024);
  const scaledNote = requests.at(-1).messages.flatMap(message => Array.isArray(message.content) ? message.content : [])
    .find(part => part.type === 'text' && part.text.includes('copy scaled from the 1600x1000 original'));
  assert.ok(scaledNote, 'The model is told it receives a scaled copy');
  checks.push('Large screenshot paste: attached instead of refused, preview and model receive a scaled copy, original downloads');

  // Dragging files into Home uses the durable draft upload, not model Send.
  // Unsupported binary data remains outside the draft and cannot navigate away.
  await page.evaluate(() => {
    const target = document.querySelector('.composer textarea');
    const transfer = new DataTransfer();
    transfer.items.add(new File(['binary'], 'unknown.exe', { type: 'application/octet-stream' }));
    const event = new DragEvent('drop', { dataTransfer: transfer, bubbles: true, cancelable: true });
    if (target.dispatchEvent(event)) throw new Error('File drop should not navigate away');
  });
  await page.locator('.composer-hint').getByText('Unsupported file.', { exact: false }).waitFor();
  assert.equal(await page.locator('.composer-materials li').count(), 0, 'Unsupported binary drops do not change the draft');

  const droppedBytes = examples.get('JPEG');
  const beforeDrop = seenPixels.length;
  await page.locator('.composer textarea').fill('Inspect the dropped photograph.');
  await page.locator('.composer-hint').getByText('Paste or drop text, PDFs and images', { exact: false }).waitFor();
  const droppedUpload = page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes('/materials?'));
  await page.evaluate(encoded => {
    const bytes = Uint8Array.from(atob(encoded), character => character.charCodeAt(0));
    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], 'Dropped photo.jpg', { type: 'image/jpeg' }));
    const target = document.querySelector('.composer textarea');
    const event = new DragEvent('dragover', { dataTransfer: transfer, bubbles: true, cancelable: true });
    if (target.dispatchEvent(event)) throw new Error('Image dragover must be accepted');
  }, droppedBytes.toString('base64'));
  await page.locator('.composer.composer-drag-target').waitFor();
  await page.locator('.composer-hint').getByText('Drop files to attach', { exact: false }).waitFor();
  await page.evaluate(encoded => {
    const bytes = Uint8Array.from(atob(encoded), character => character.charCodeAt(0));
    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], 'Dropped photo.jpg', { type: 'image/jpeg' }));
    const target = document.querySelector('.composer textarea');
    const event = new DragEvent('drop', { dataTransfer: transfer, bubbles: true, cancelable: true });
    if (target.dispatchEvent(event)) throw new Error('Image drop must not navigate away');
  }, droppedBytes.toString('base64'));
  const droppedReceipt = await (await droppedUpload).json();
  assert.equal(droppedReceipt.material.sha256, createHash('sha256').update(droppedBytes).digest('hex'));
  await page.getByRole('button', { name: 'Inspect Dropped photo.jpg before sending' }).waitFor();
  assert.equal(seenPixels.length, beforeDrop, 'Dropping an image cannot send a model turn');
  await page.reload();
  await page.getByRole('button', { name: 'Inspect Dropped photo.jpg before sending' }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Inspect the dropped photograph.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => seenPixels.length === beforeDrop + 1 &&
    (await view()).messages.at(-1)?.role === 'assistant', 'dropped image reaches native model request');
  assert.ok(seenPixels.at(-1).equals(droppedBytes));
  checks.push('Home image drag/drop: visible target, unsupported-binary feedback, durable unsent draft, exact image bytes only on Send');

  // A single drop containing several supported file kinds attaches every
  // file to the same unsent draft, in order. The native HTTP upload and source
  // receipts remain authoritative; the model sees nothing until Send.
  const multiBefore = seenPixels.length;
  const multiRequestsBefore = requests.length;
  const mixedText = 'Cross-source notes from a single drop.\\n';
  await page.locator('.composer textarea').fill('Compare these three original sources.');
  await page.evaluate(([png, jpeg, note]) => {
    const data = new DataTransfer();
    const bytes = encoded => Uint8Array.from(atob(encoded), ch => ch.charCodeAt(0));
    data.items.add(new File([bytes(png)], 'Batch diagram.png', { type: 'image/png' }));
    data.items.add(new File([bytes(jpeg)], 'Batch photo.jpg', { type: 'image/jpeg' }));
    data.items.add(new File([note], 'Batch notes.txt', { type: 'text/plain' }));
    const target = document.querySelector('.composer textarea');
    if (target.dispatchEvent(new DragEvent('drop', { dataTransfer: data, bubbles: true, cancelable: true }))) {
      throw new Error('Multi-file drop must not navigate away');
    }
  }, [pastedBytes.toString('base64'), droppedBytes.toString('base64'), mixedText]);
  await wait(async () => await page.locator('.composer-materials li').count() === 3
    && !(await page.getByRole('button', { name: 'Send message', exact: true }).isDisabled()),
  'three mixed drop attachments ready');
  assert.equal(requests.length, multiRequestsBefore, 'A file drop cannot invoke the model');
  await page.reload();
  for (const name of ['Batch diagram.png', 'Batch photo.jpg', 'Batch notes.txt']) {
    await page.getByRole('button', { name: 'Inspect ' + name + ' before sending' }).waitFor();
  }
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Compare these three original sources.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => seenPixels.length === multiBefore + 2 && (await view()).messages.at(-1)?.role === 'assistant',
    'both images reach the native model after explicit Send');
  assert.ok(seenPixels[multiBefore].equals(pastedBytes));
  assert.ok(seenPixels[multiBefore + 1].equals(droppedBytes));
  const batchMessage = [...(await view()).messages].reverse()
    .find(message => message.materials?.some(material => material.name === 'Batch notes.txt'));
  assert.deepEqual(batchMessage.materials.map(material => material.name),
    ['Batch diagram.png', 'Batch photo.jpg', 'Batch notes.txt']);
  checks.push('Three-file image/text drop: all native receipts in one unsent draft, reload persistence, two exact model images and source materials on Send');

  // The sixth file is left unprocessed with a visible partial note. The
  // first five remain removable unsent attachments; no model request occurs.
  const beforeFull = requests.length;
  await page.evaluate(() => {
    const data = new DataTransfer();
    for (let index = 0; index < 6; index++) {
      data.items.add(new File(['draft-' + index], 'Batch limit ' + index + '.txt', { type: 'text/plain' }));
    }
    const target = document.querySelector('.composer textarea');
    if (target.dispatchEvent(new DragEvent('drop', { dataTransfer: data, bubbles: true, cancelable: true }))) {
      throw new Error('Six-file drop must not navigate away');
    }
  });
  await wait(async () => await page.locator('.composer-materials li').count() === 5
    && !(await page.getByRole('button', { name: 'Send message', exact: true }).isDisabled()),
  'five attachments and completed bounded batch');
  await page.getByText('Added 5 file(s) before stopping.', { exact: false }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Inspect Batch limit 5.txt before sending' }).count(), 0);
  assert.equal(requests.length, beforeFull);
  for (let index = 0; index < 5; index++) await page.locator('.composer-material-remove').first().click();
  assert.equal(await page.locator('.composer-materials li').count(), 0);
  checks.push('Overflow drop attaches five, reports the unprocessed sixth file and keeps draft edits under user control');

  // The same composer path works inside a Work. Its attachment must not
  // appear in Home merely because their files share the World workspace.
  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill('Screenshot follow-up');
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill('Inspect an image in this Work.');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Screenshot follow-up', exact: true }).waitFor();
  const workId = new URL(page.url()).pathname.split('/').pop();
  const workCount = seenPixels.length;
  await page.locator('.composer textarea').fill('Explain this screenshot in the Work.');
  const workUpload = page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes('/materials?'));
  await page.evaluate(encoded => {
    const bytes = Uint8Array.from(atob(encoded), character => character.charCodeAt(0));
    const clipboard = new DataTransfer();
    clipboard.items.add(new File([bytes], 'Work image.png', { type: 'image/png' }));
    document.querySelector('.composer textarea').dispatchEvent(new ClipboardEvent('paste', {
      clipboardData: clipboard, bubbles: true, cancelable: true,
    }));
  }, pastedBytes.toString('base64'));
  const workReceipt = await (await workUpload).json();
  assert.equal(workReceipt.material.pageId, workId);
  await page.getByRole('button', { name: 'Inspect Work image.png before sending' }).waitFor();
  assert.equal(seenPixels.length, workCount, 'Work paste must not submit a model turn');
  await page.goto(base);
  await page.locator('.composer textarea').waitFor();
  assert.equal(await page.getByRole('button', { name: 'Inspect Work image.png before sending' }).count(), 0,
    'Home does not inherit the Work draft');
  await page.goto(base + '/page/' + workId);
  await page.getByRole('button', { name: 'Inspect Work image.png before sending' }).waitFor();
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => seenPixels.length === workCount + 1 &&
    (await fetch(base + '/api/pages/' + workId).then(response => response.json())).messages.at(-1)?.role === 'assistant',
    'Work image reaches native model request');
  assert.ok(seenPixels.at(-1).equals(pastedBytes));
  checks.push('Work paste: original Work draft survives navigation; Home isolation; original image only on explicit Send');
  await page.goto(base);
  await page.locator('.composer textarea').waitFor();

  await open();
  const picker = page.getByLabel('Choose a file', { exact: true });
  await picker.setInputFiles({ name: 'broken.png', mimeType: 'image/png', buffer: Buffer.from('broken image') });
  await page.getByRole('button', { name: 'Upload file', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'This image could not be read.' }).waitFor();
  await picker.setInputFiles({ name: 'large.jpg', mimeType: 'image/jpeg', buffer: Buffer.alloc(32 * 1024 * 1024 + 1) });
  await page.getByRole('button', { name: 'Upload file', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'PNG/JPEG image or PDF of at most 32 MiB' }).waitFor();
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
