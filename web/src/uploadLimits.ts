/** Upload limits; the server applies the same ones. */
export const MAX_TEXT_BYTES = 1024 * 1024;
export const MAX_PDF_BYTES = 32 * 1024 * 1024;
export const MAX_IMAGE_BYTES = 32 * 1024 * 1024;

export function isPdfName(name: string): boolean {
  return name.toLowerCase().endsWith('.pdf');
}

export function isImageName(name: string): boolean {
  return /\.(png|jpe?g)$/i.test(name);
}

/** Match the same kinds the Materials picker accepts before dropping files.
 * The native upload still validates bytes and source identity.
 */
export function isSupportedUpload(file: { name: string; type: string }): boolean {
  return isImageName(file.name) || isPdfName(file.name)
    || /\.(txt|md|markdown|csv|tsv|json|yaml|yml|log|py|ts|js|html|css)$/i.test(file.name)
    || file.type.startsWith('text/')
    || file.type === 'image/png' || file.type === 'image/jpeg' || file.type === 'application/pdf';
}

export function uploadLimit(name: string): number {
  if (isPdfName(name)) return MAX_PDF_BYTES;
  return isImageName(name) ? MAX_IMAGE_BYTES : MAX_TEXT_BYTES;
}

export const UPLOAD_ACCEPT = 'text/*,.txt,.md,.markdown,.csv,.tsv,.json,.yaml,.yml,.log,.py,.ts,.js,.html,.css,.pdf,application/pdf,.png,.jpg,.jpeg,image/png,image/jpeg';

/** Whether a PDF's extracted text has more than its [Page N] lines. */
export function pdfHasText(content: string): boolean {
  return content.replace(/^\[Page \d+\]$/gm, '').trim().length > 0;
}
