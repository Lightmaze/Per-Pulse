/** A bounded reading view for comma- and tab-separated text.
 * Values stay strings: no numeric conversion, formula evaluation or HTML.
 * Quoting follows RFC 4180; LF/CR records and a UTF-8 BOM are also accepted.
 */
export const TABLE_RECORD_LIMIT = 201;
export const TABLE_COLUMN_LIMIT = 64;

export type DelimitedPreview = {
  records: string[][];
  partial: boolean;
} | { error: 'malformed' | 'tooWide' };

export function parseDelimitedPreview(content: string, delimiter: ',' | '\t', truncated = false): DelimitedPreview {
  const text = content.startsWith('\uFEFF') ? content.slice(1) : content;
  const records: string[][] = [];
  let row: string[] = [], field = '', state: 'start' | 'plain' | 'quoted' | 'closed' = 'start';
  const append = () => {
    row.push(field); field = ''; state = 'start';
    return row.length <= TABLE_COLUMN_LIMIT;
  };
  const record = () => {
    if (!append()) return 'tooWide' as const;
    if (records.length && row.length !== records[0].length) return 'malformed' as const;
    records.push(row); row = [];
    return null;
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (state === 'quoted') {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else state = 'closed';
      } else field += c;
      continue;
    }
    if (c === delimiter) {
      if (!append()) return { error: 'tooWide' };
    } else if (c === '\r' || c === '\n') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      const error = record();
      if (error) return { error };
      if (records.length === TABLE_RECORD_LIMIT) {
        return { records, partial: truncated || i + 1 < text.length };
      }
    } else if (c === '"' && state === 'start') state = 'quoted';
    else if (c === '"' || state === 'closed') return { error: 'malformed' };
    else { field += c; state = 'plain'; }
  }
  // A server-limited preview may end in the middle of any field, including
  // an unquoted one. Only newline-terminated records are safe in that case.
  if (truncated) return { records, partial: true };
  if (state === 'quoted') return { error: 'malformed' };
  if (row.length || field || state !== 'start') {
    const error = record();
    if (error) return { error };
  }
  return { records, partial: false };
}
