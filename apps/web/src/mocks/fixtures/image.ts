// Плейсхолдер-картинка бутылки: инлайновый SVG, без сети — оффлайн-надёжность и Lighthouse
// не страдают от внешних хостов. Цвет параметризован под категорию вина, хексы — не из
// tokens.css: это статичный ассет фикстур, а не элемент UI, токены тут неприменимы (нет
// доступа к CSS-переменным вне каскада документа).
export function bottlePlaceholderDataUri(hex: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 320">
    <rect width="120" height="320" fill="none" />
    <path d="M50 10h20v40l14 24v220a10 10 0 0 1-10 10H46a10 10 0 0 1-10-10V74l14-24z" fill="${hex}" opacity="0.85" />
    <rect x="40" y="120" width="40" height="70" fill="#FBF9F7" opacity="0.85" />
  </svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}
