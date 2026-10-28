export const documentsEnglish = {
  'document.view': 'Document view',
  'document.read': 'Read',
  'document.source': 'Source',
  'document.newTab': '(opens a new tab)',
  'document.image': 'Image: {label}',
  'document.unnamedImage': 'no description',
  'document.table': 'Table in document',
  'document.unavailable': 'The reading view could not be opened. The source text is available below.',
};

export const documentsChinese: Record<keyof typeof documentsEnglish, string> = {
  'document.view': '文档视图',
  'document.read': '阅读',
  'document.source': '原文',
  'document.newTab': '（在新标签页打开）',
  'document.image': '图片：{label}',
  'document.unnamedImage': '没有说明',
  'document.table': '文档中的表格',
  'document.unavailable': '无法打开阅读视图，下方仍可查看原文。',
};
