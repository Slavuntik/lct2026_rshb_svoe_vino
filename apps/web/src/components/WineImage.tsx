import { useState } from "react";

interface WineImageProps {
  /** Каталожные image_url — хотлинк на vino-svoe.ru (данные vines), не наш хост. */
  src?: string;
  alt: string;
  width?: number;
  className?: string;
}

/**
 * <img> с гардом от битых хотлинков (ревью 03, риск 1): при отсутствии src или ошибке
 * загрузки — нейтральный инлайн-SVG силуэт бутылки на токенах, а не сломанная иконка
 * браузера в кадре демо. SVG рендерится живым элементом DOM (не data-URI <img src>),
 * поэтому fill="var(--muted)" реально резолвится из tokens.css и переживает смену темы —
 * без единого хардкода цвета.
 */
export function WineImage({ src, alt, width = 80, className }: WineImageProps) {
  const [broken, setBroken] = useState(false);

  if (!src || broken) {
    return (
      <svg
        viewBox="0 0 120 320"
        width={width}
        role="img"
        aria-label={alt}
        className={className}
        data-testid="wine-image-placeholder"
      >
        <rect width="120" height="320" fill="var(--mono-bg)" rx="8" />
        <path
          d="M50 10h20v40l14 24v220a10 10 0 0 1-10 10H46a10 10 0 0 1-10-10V74l14-24z"
          fill="var(--muted)"
        />
        <rect x="40" y="120" width="40" height="70" fill="var(--card)" opacity="0.7" />
      </svg>
    );
  }

  return <img src={src} alt={alt} width={width} className={className} onError={() => setBroken(true)} />;
}
