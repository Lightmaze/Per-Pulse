// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import { useLanguage } from '../i18n';
import type { ImageMetadata } from '../types';

export function ImagePreview({ name, image, source }: { name: string; image?: ImageMetadata; source?: string }) {
  const { t } = useLanguage();
  if (!image || !source || !/^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(source)) {
    return <p className="form-error">{t('library.imageUnavailable')}</p>;
  }
  return <figure className="material-image">
    <img src={source} alt={name} width={image.width} height={image.height} />
    <figcaption>
      <span>{image.mime === 'image/png' ? 'PNG' : 'JPEG'} · {image.width.toLocaleString()} × {image.height.toLocaleString()}</span>
      <p className="material-hint">{t('library.imageModel')}</p>
    </figcaption>
  </figure>;
}
