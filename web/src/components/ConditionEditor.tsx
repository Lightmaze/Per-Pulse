// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import { useLanguage } from '../i18n';
import { conditionIssue, replaceCondition, type ConditionPath } from '../lessonConditions';
import type { LessonCondition } from '../types';
import './ConditionEditor.css';

function ConditionNode({ value, path, prefix, disabled, change, remove }: {
  value: LessonCondition; path: ConditionPath; prefix: string; disabled: boolean;
  change: (path: ConditionPath, value: LessonCondition) => void; remove?: () => void;
}) {
  const { t } = useLanguage();
  const position = path.length ? path.map(index => index + 1).join('.') : '1';
  const id = `${prefix}-${path.length ? path.join('-') : 'root'}`;
  const label = path.length ? t('learning.conditionNumber', { number: position }) : t('learning.refineConditions');
  const exclusion = typeof value !== 'string' && 'not' in value;
  const children = typeof value === 'string' ? null : 'not' in value ? [value.not] : 'all' in value ? value.all : value.any;
  return <fieldset className="condition-node" disabled={disabled}>
    <legend>{label}</legend>
    {typeof value === 'string' ? <textarea aria-label={label} id={id} rows={2} value={value}
      onChange={event => change(path, event.target.value)} /> : exclusion ? <p className="lesson-note">{t('learning.conditionNot')}</p> : <>
      <label className="field-label" htmlFor={`${id}-match`}>{t('learning.conditionMatch')}</label>
      <select id={`${id}-match`} value={'all' in value ? 'all' : 'any'}
        onChange={event => change(path, event.target.value === 'all' ? { all: children! } : { any: children! })}>
        <option value="all">{t('learning.conditionAll')}</option>
        <option value="any">{t('learning.conditionAny')}</option>
      </select>
    </>}
    {children?.map((child, index) => <ConditionNode key={index} value={child} path={[...path, index]}
      prefix={prefix} disabled={disabled} change={change}
      remove={!exclusion && children.length > 1 ? () => {
        const remaining = children.filter((_, offset) => offset !== index);
        change(path, remaining.length === 1 ? remaining[0] : typeof value !== 'string' && 'all' in value ? { all: remaining } : { any: remaining });
      } : undefined} />)}
    <div className="lesson-actions condition-actions">
      {!exclusion ? <button className="text-button" type="button" disabled={path.length >= 6 || (children?.length ?? 0) >= 12}
        onClick={() => change(path, typeof value === 'string' ? { all: [value, ''] } : 'all' in value ? { all: [...value.all, ''] } : 'any' in value ? { any: [...value.any, ''] } : value)}>
        {t('learning.addCondition')}
      </button> : null}
      <button className="text-button" type="button" disabled={!exclusion && path.length >= 6}
        onClick={() => change(path, typeof value !== 'string' && 'not' in value ? value.not : { not: value })}>
        {t(exclusion ? 'learning.includeCondition' : 'learning.excludeCondition')}
      </button>
      {remove ? <button className="text-button" type="button" onClick={remove}>{t('learning.removeCondition')}</button> : null}
    </div>
  </fieldset>;
}

export function ConditionEditor({ value, onChange, disabled, id }: {
  value: LessonCondition; onChange: (value: LessonCondition) => void; disabled: boolean; id: string;
}) {
  const { t } = useLanguage();
  const issue = conditionIssue(value);
  return <div className="condition-editor">
    <p className="lesson-note">{t('learning.conditionsHelp')}</p>
    <ConditionNode value={value} path={[]} prefix={id} disabled={disabled}
      change={(path, next) => onChange(replaceCondition(value, path, next))} />
    {issue ? <p className="form-error" role="status">{t(issue === 'empty' ? 'learning.conditionEmpty' : issue === 'long' ? 'learning.conditionLong' : 'learning.conditionComplex')}</p> : null}
  </div>;
}
