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

export function uploadLimit(name: string): number {
  if (isPdfName(name)) return MAX_PDF_BYTES;
  return isImageName(name) ? MAX_IMAGE_BYTES : MAX_TEXT_BYTES;
}

export const UPLOAD_ACCEPT = 'text/*,.txt,.md,.markdown,.csv,.tsv,.json,.yaml,.yml,.log,.py,.ts,.js,.html,.css,.pdf,application/pdf,.png,.jpg,.jpeg,image/png,image/jpeg';

/** Whether a PDF's extracted text has more than its [Page N] lines. */
export function pdfHasText(content: string): boolean {
  return content.replace(/^\[Page \d+\]$/gm, '').trim().length > 0;
}
