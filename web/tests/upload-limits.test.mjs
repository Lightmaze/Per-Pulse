import assert from 'node:assert/strict';
import test from 'node:test';
import { MAX_PDF_BYTES, MAX_TEXT_BYTES, UPLOAD_ACCEPT, isPdfName, pdfHasText, isSupportedUpload, uploadLimit } from '../src/uploadLimits.ts';

test('PDFs may be larger than text files, matching the server', () => {
  assert.equal(MAX_TEXT_BYTES, 1024 * 1024);
  assert.equal(MAX_PDF_BYTES, 32 * 1024 * 1024);
  assert.equal(uploadLimit('notes.md'), MAX_TEXT_BYTES);
  assert.equal(uploadLimit('Q3 Report.PDF'), MAX_PDF_BYTES);
  assert.equal(isPdfName('report.pdf.txt'), false);
  assert.match(UPLOAD_ACCEPT, /\.pdf/);
});

test('a PDF with only page lines has no text to show', () => {
  assert.equal(pdfHasText('[Page 1]\n\n[Page 2]'), false);
  assert.equal(pdfHasText('[Page 1]\nRevenue grew.'), true);
  assert.equal(pdfHasText('[Page 1]\nSee [Page 3] for details.'), true);
});

test('composer drag/drop accepts supported document types and rejects unrelated binary files', () => {
  for (const [name, type] of [
    ['field-notes.txt', 'text/plain'],
    ['results.CSV', 'text/csv'],
    ['paper.pdf', 'application/pdf'],
    ['frame.png', 'image/png'],
    ['photo.jpeg', 'image/jpeg'],
    ['snippet.ts', 'text/plain'],
  ]) assert.equal(isSupportedUpload({ name, type }), true, name);
  assert.equal(isSupportedUpload({ name: 'program.exe', type: 'application/octet-stream' }), false);
});
