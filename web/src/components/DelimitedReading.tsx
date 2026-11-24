import { useId, useMemo, type Dispatch, type SetStateAction, type ReactNode } from 'react';
import { parseDelimitedPreview } from '../delimitedText';
import { useLanguage } from '../i18n';

export function DelimitedReading({ content, delimiter, truncated, fallback, headings, setHeadings, hiddenColumns, setHiddenColumns }: {
  content: string; delimiter: ',' | '\t'; truncated: boolean; fallback: ReactNode;
  headings: boolean; setHeadings: (value: boolean) => void;
  hiddenColumns: number[]; setHiddenColumns: Dispatch<SetStateAction<number[]>>;
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
  const visibleColumns = labels.map((_, index) => index).filter(index => !hiddenColumns.includes(index));
  return <div className="delimited-document">
    <label className="table-header-option"><input type="checkbox" checked={headings} onChange={event => setHeadings(event.target.checked)} />{t('document.firstRowHeadings')}</label>
    <details className="table-columns">
      <summary id={`${id}-columns`}>{t('document.columnsShown', { shown: visibleColumns.length, total: columns })}</summary>
      <p className="material-hint">{t('document.columnsHint')}</p>
      <div className="table-column-actions"><button type="button" className="text-button" onClick={() => setHiddenColumns([])}>{t('document.showAllColumns')}</button><button type="button" className="text-button" onClick={() => setHiddenColumns(labels.map((_, index) => index))}>{t('document.hideAllColumns')}</button></div>
      <fieldset><legend className="visually-hidden">{t('document.visibleColumns')}</legend><div className="table-column-options">{labels.map((label, index) => <label key={index}>
        <input type="checkbox" checked={!hiddenColumns.includes(index)} onChange={event => {
          const show = event.target.checked;
          setHiddenColumns(current => show ? current.filter(column => column !== index) : [...current, index]);
        }} /><span>{t('document.columnChoice', { number: index + 1, name: label || t('document.column', { number: index + 1 }) })}</span>
      </label>)}</div></fieldset>
    </details>
    <p id={`${id}-summary`} className="table-summary">{t(partial ? 'document.tablePartial' : 'document.tableSize', { rows: rows.length, columns })}</p>
    {visibleColumns.length ? <div className="delimited-scroll" tabIndex={0} role="region" aria-label={t('document.table')} aria-describedby={`${id}-summary ${id}-columns`}>
      <table>
        <caption className="visually-hidden">{t('document.table')}</caption>
        <thead><tr>{visibleColumns.map(index => <th key={index} scope="col">{labels[index] || t('document.column', { number: index + 1 })}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index}>{visibleColumns.map(column => <td key={column}>{row[column]}</td>)}</tr>)}</tbody>
      </table>
    </div> : <p className="table-summary" role="status">{t('document.noColumns')}</p>}
  </div>;
}
