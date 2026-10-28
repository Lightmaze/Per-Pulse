import { useId, useMemo, type ReactNode } from 'react';
import { parseDelimitedPreview } from '../delimitedText';
import { useLanguage } from '../i18n';

export function DelimitedReading({ content, delimiter, truncated, fallback, headings, setHeadings }: {
  content: string; delimiter: ',' | '\t'; truncated: boolean; fallback: ReactNode;
  headings: boolean; setHeadings: (value: boolean) => void;
}) {
  const { t } = useLanguage();
  const id = useId();
  const result = useMemo(() => parseDelimitedPreview(content, delimiter, truncated), [content, delimiter, truncated]);
  if ('error' in result) return <><p role="status">{t(result.error === 'tooWide' ? 'document.tableTooWide' : 'document.tableInvalid')}</p>{fallback}</>;
  if (!result.records.length) return <><p role="status">{t('document.tableIncomplete')}</p>{fallback}</>;
  const columns = result.records[0].length;
  const labels = headings ? result.records[0] : Array.from({ length: columns }, (_, i) => t('document.column', { number: i + 1 }));
  const rows = (headings ? result.records.slice(1) : result.records).slice(0, 200);
  const partial = result.partial || (!headings && result.records.length > 200);
  return <div className="delimited-document">
    <label className="table-header-option"><input type="checkbox" checked={headings} onChange={event => setHeadings(event.target.checked)} />{t('document.firstRowHeadings')}</label>
    <p id={`${id}-summary`} className="table-summary">{t(partial ? 'document.tablePartial' : 'document.tableSize', { rows: rows.length, columns })}</p>
    <div className="delimited-scroll" tabIndex={0} role="region" aria-label={t('document.table')} aria-describedby={`${id}-summary`}>
      <table>
        <caption className="visually-hidden">{t('document.table')}</caption>
        <thead><tr>{labels.map((label, index) => <th key={index} scope="col">{label || t('document.column', { number: index + 1 })}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index}>{row.map((value, column) => <td key={column}>{value}</td>)}</tr>)}</tbody>
      </table>
    </div>
  </div>;
}
