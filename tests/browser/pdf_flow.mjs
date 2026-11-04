/** A PDF can be uploaded as a material and previewed as its text.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 * The PDFs are synthetic; uploads, the workspace and the page service are real. No model is used.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const screenshots = process.env.PER_PULSE_PDF_SCREENSHOTS;
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-pdf-'));
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
const env = { ...process.env };
for (const key of ['P_PULSE_CHAT_ENDPOINT', 'P_PULSE_CHAT_MODEL', 'P_PULSE_API_KEY', 'P_PULSE_CREDENTIAL_ENV']) delete env[key];
let serverLog = '', browser;
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'),
  ['-m', 'per_pulse.cli', '--data-dir', directory, '--port', String(port)], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
server.stdout.on('data', data => { serverLog += data; });
server.stderr.on('data', data => { serverLog += data; });
const wait = async (fn, label, timeout = 20000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await fn()) return; } catch { /* Wait for the actual state. */ }
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const files = () => readdir(join(directory, 'workspace/uploads')).catch(() => []);

/** A small valid PDF with one line of ASCII text per page. */
function syntheticPdf(pages) {
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>'];
  const pageIds = pages.map((_, index) => 4 + 2 * index);
  objects.push(`<< /Type /Pages /Kids [${pageIds.map(id => `${id} 0 R`).join(' ')}] /Count ${pages.length} >>`);
  objects.push('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');
  pages.forEach((text, index) => {
    const stream = `BT /F1 12 Tf 72 720 Td (${text.replace(/[\\()]/g, char => '\\' + char)}) Tj ET`;
    objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents ${pageIds[index] + 1} 0 R >>`);
    objects.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
  });
  let out = '%PDF-1.4\n';
  const offsets = objects.map((body, index) => { const at = out.length; out += `${index + 1} 0 obj\n${body}\nendobj\n`; return at; });
  const xref = out.length;
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n` + offsets.map(at => `${String(at).padStart(10, '0')} 00000 n \n`).join('');
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, 'latin1');
}

const checks = [];
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'en-US' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.locator('.composer textarea').waitFor();
  await page.getByRole('button', { name: 'Materials', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Materials', exact: true });
  await dialog.waitFor();
  const picker = dialog.getByLabel('Choose a file', { exact: true });
  assert.match(await picker.getAttribute('accept'), /\.pdf/);

  await picker.setInputFiles({ name: 'broken.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\nnot really a pdf\n') });
  await dialog.getByRole('button', { name: 'Upload file', exact: true }).click();
  await dialog.getByRole('alert').filter({ hasText: 'This PDF could not be read.' }).waitFor();
  assert.equal((await files()).length, 0);
  checks.push('a PDF that cannot be read is refused with its reason, and no file is kept');

  const report = syntheticPdf(['Quarterly revenue grew 12 percent.', 'Costs fell in the second half.']);
  await picker.setInputFiles({ name: 'Q3 report.pdf', mimeType: 'application/pdf', buffer: report });
  await dialog.getByRole('button', { name: 'Upload file', exact: true }).click();
  const preview = dialog.locator('.material-preview');
  await preview.getByText('The text of this PDF, page by page.', { exact: true }).waitFor();
  assert.equal(await preview.locator('pre').innerText(),
    '[Page 1]\nQuarterly revenue grew 12 percent.\n\n[Page 2]\nCosts fell in the second half.');
  const [stored] = await files();
  assert.deepEqual(await readFile(join(directory, 'workspace/uploads', stored)), report);
  checks.push('an uploaded PDF is kept as the original file and previewed as its text, page by page');
  if (screenshots) { await mkdir(screenshots, { recursive: true }); await page.screenshot({ path: join(screenshots, 'pdf-preview.png') }); }

  await dialog.getByRole('button', { name: 'Add to draft', exact: true }).click();
  await page.getByRole('button', { name: 'Remove Q3 report.pdf from this message', exact: true }).waitFor();
  checks.push('the PDF can be attached to a message');

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks }));
} finally {
  await browser?.close();
  server.kill('SIGTERM');
  await new Promise(done => server.once('exit', done));
  await rm(directory, { recursive: true, force: true });
}
