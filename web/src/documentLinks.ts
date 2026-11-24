/** Document links must be intentional external navigation, not app routes. */
export function externalDocumentLink(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return (url.protocol === 'https:' || url.protocol === 'http:') && !url.username && !url.password
      ? url.href : null;
  } catch { return null; }
}
