import assert from 'node:assert/strict';
import test from 'node:test';
import { parseDelimitedPreview } from '../src/delimitedText.ts';

test('CSV retains quoted commas, escaped quotes, embedded newlines and exact strings', () => {
  const value = '\uFEFFname,value,note\r\n"A, B",001.00,"line 1\r\nline ""2"""\r\n C ,0,\r\n';
  assert.deepEqual(parseDelimitedPreview(value, ','), {
    records: [['name', 'value', 'note'], ['A, B', '001.00', 'line 1\r\nline "2"'], [' C ', '0', '']], partial: false,
  });
});

test('TSV, LF and CR record delimiters preserve empty cells and formula-looking text', () => {
  assert.deepEqual(parseDelimitedPreview('key\tvalue\nA\t=1+1\nB\t', '\t'), {
    records: [['key', 'value'], ['A', '=1+1'], ['B', '']], partial: false,
  });
  assert.deepEqual(parseDelimitedPreview('a,b\r1,2', ','), { records: [['a', 'b'], ['1', '2']], partial: false });
  assert.deepEqual(parseDelimitedPreview('""', ','), { records: [['']], partial: false });
  assert.deepEqual(parseDelimitedPreview('', ','), { records: [], partial: false });
});

test('irregular quoting and unequal row widths fall back instead of shifting named values', () => {
  for (const value of ['a,b\n1,2,3', 'a,b\n1', 'a,b\n"unfinished', 'a,b\n"x"tail,2', 'a,b\nnon"quote,2']) {
    assert.deepEqual(parseDelimitedPreview(value, ','), { error: 'malformed' }, value);
  }
});

test('a truncated source never presents a partial trailing record as complete', () => {
  for (const suffix of ['partial,value', 'partial,"still\nquoted', 'partial,"closed"']) {
    assert.deepEqual(parseDelimitedPreview('name,value\ncomplete,001\n' + suffix, ',', true), {
      records: [['name', 'value'], ['complete', '001']], partial: true,
    });
  }
  assert.deepEqual(parseDelimitedPreview('name,val', ',', true), { records: [], partial: true });
});

test('table rendering is bounded by complete records and refuses very wide data', () => {
  const header = 'name,value\n';
  const records = Array.from({ length: 201 }, (_, i) => `row${i},${i}\n`).join('');
  const result = parseDelimitedPreview(header + records, ',');
  assert.equal(result.records.length, 201);
  assert.equal(result.partial, true);
  assert.deepEqual(result.records.at(-1), ['row199', '199']);
  const exact = parseDelimitedPreview(header + records.split('\n').slice(0, 200).join('\n') + '\n', ',');
  assert.equal(exact.partial, false);
  assert.equal(exact.records.length, 201);
  assert.deepEqual(parseDelimitedPreview(Array.from({ length: 65 }, () => 'value').join(','), ','), { error: 'tooWide' });
});
